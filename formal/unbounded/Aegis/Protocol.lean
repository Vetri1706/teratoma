import Std
import Aegis.Quorum

namespace Aegis

abbrev Log (Command : Type) := List Command

structure Snapshot (Command : Type) where
  view : Nat
  log : Log Command

structure Vote (n : Nat) (Command : Type) where
  sender : Fin n
  view : Nat
  log : Log Command

structure Report (n : Nat) (Command : Type) where
  sender : Fin n
  view : Nat
  accepted : Snapshot Command

structure Proposal (Command : Type) where
  view : Nat
  log : Log Command

structure Election (n : Nat) (Command : Type) where
  view : Nat
  voters : Fin n → Bool
  reports : Fin n → Snapshot Command
  selected : Fin n

def Election.base (e : Election n Command) : Log Command :=
  (e.reports e.selected).log

structure Node (Command : Type) where
  promise : Nat
  accepted : Snapshot Command
  changing : Bool
  alive : Bool
  applied : Snapshot Command

structure State (n : Nat) (Command : Type) where
  nodes : Fin n → Node Command
  votes : List (Vote n Command)
  reports : List (Report n Command)
  proposals : List (Proposal Command)
  elections : List (Election n Command)
  commits : List (Proposal Command)

def initial : State n Command :=
  { nodes := fun _ => ⟨0, ⟨0, []⟩, false, true, ⟨0, []⟩⟩
    votes := [], reports := [], proposals := [⟨0, []⟩], elections := [], commits := [] }

def Certified (s : State n Command) (v : Nat) (p : Log Command) : Prop :=
  ∃ q : Fin n → Bool, Majority q ∧
    ∀ r, q r = true → ∃ l, Vote.mk r v l ∈ s.votes ∧ p <+: l

def ElectionValid (s : State n Command) (e : Election n Command) : Prop :=
  Majority e.voters ∧ e.voters e.selected = true ∧
  (∀ r, e.voters r = true → Report.mk r e.view (e.reports r) ∈ s.reports) ∧
  (∀ r, e.voters r = true →
    (e.reports r).view ≤ (e.reports e.selected).view ∧
    ((e.reports r).view = (e.reports e.selected).view →
      (e.reports r).log.length ≤ e.base.length))

structure HistoryFacts (s : State n Command) : Prop where
  proposals_origin : ∀ p ∈ s.proposals,
    p.view = 0 ∨ ∃ e ∈ s.elections, e.view = p.view ∧ e.base <+: p.log
  proposals_comparable : ∀ p ∈ s.proposals, ∀ q ∈ s.proposals,
    p.view = q.view → p.log <+: q.log ∨ q.log <+: p.log
  votes_proposed : ∀ v ∈ s.votes, Proposal.mk v.view v.log ∈ s.proposals
  reports_proposed : ∀ r ∈ s.reports,
    Proposal.mk r.accepted.view r.accepted.log ∈ s.proposals
  reports_lower : ∀ r ∈ s.reports, r.accepted.view < r.view
  reports_fence : ∀ r ∈ s.reports, ∀ v ∈ s.votes,
    v.sender = r.sender → v.view < r.view →
    v.view ≤ r.accepted.view ∧ (v.view = r.accepted.view → v.log <+: r.accepted.log)
  elections_valid : ∀ e ∈ s.elections, ElectionValid s e
  elections_proposed : ∀ e ∈ s.elections, Proposal.mk e.view e.base ∈ s.proposals

def updateNode (s : State n Command) (r : Fin n) (node : Node Command) : State n Command :=
  { s with nodes := fun i => if i = r then node else s.nodes i }

inductive Step (primary : Nat → Fin n) : State n Command → State n Command → Prop where
  | beginView (s : State n Command) (r : Fin n) (v : Nat)
      (alive : (s.nodes r).alive = true) (newer : (s.nodes r).promise < v) :
      Step primary s (updateNode s r { s.nodes r with promise := v, changing := true })
  | sendReport (s : State n Command) (r : Fin n)
      (alive : (s.nodes r).alive = true) (changing : (s.nodes r).changing = true) :
      Step primary s { s with reports := ⟨r, (s.nodes r).promise, (s.nodes r).accepted⟩ :: s.reports }
  | propose (s : State n Command) (v : Nat) (command : Command)
      (alive : (s.nodes (primary v)).alive = true)
      (normal : (s.nodes (primary v)).changing = false)
      (view : (s.nodes (primary v)).promise = v) :
      Step primary s
        { updateNode s (primary v)
            { s.nodes (primary v) with accepted := ⟨v, (s.nodes (primary v)).accepted.log ++ [command]⟩ }
          with proposals := ⟨v, (s.nodes (primary v)).accepted.log ++ [command]⟩ :: s.proposals }
  | elect (s : State n Command) (e : Election n Command)
      (alive : (s.nodes (primary e.view)).alive = true)
      (changing : (s.nodes (primary e.view)).changing = true)
      (view : (s.nodes (primary e.view)).promise = e.view)
      (certificate : ElectionValid s e) :
      Step primary s
        { updateNode s (primary e.view)
            { s.nodes (primary e.view) with accepted := ⟨e.view, e.base⟩, changing := false }
          with proposals := ⟨e.view, e.base⟩ :: s.proposals, elections := e :: s.elections }
  | accept (s : State n Command) (r : Fin n) (p : Proposal Command)
      (message : p ∈ s.proposals) (backup : r ≠ primary p.view)
      (alive : (s.nodes r).alive = true) (view : (s.nodes r).promise ≤ p.view)
      (extendSameView : (s.nodes r).accepted.view = p.view → (s.nodes r).accepted.log <+: p.log) :
      Step primary s (updateNode s r
        { s.nodes r with promise := p.view, accepted := ⟨p.view, p.log⟩, changing := false })
  | acknowledge (s : State n Command) (r : Fin n)
      (alive : (s.nodes r).alive = true) (normal : (s.nodes r).changing = false) :
      Step primary s { s with votes := ⟨r, (s.nodes r).accepted.view, (s.nodes r).accepted.log⟩ :: s.votes }
  | commit (s : State n Command) (p : Proposal Command)
      (alive : (s.nodes (primary p.view)).alive = true)
      (normal : (s.nodes (primary p.view)).changing = false)
      (view : (s.nodes (primary p.view)).promise = p.view)
      (proposed : p ∈ s.proposals) (certificate : Certified s p.view p.log) :
      Step primary s { s with commits := p :: s.commits }
  | applyCommit (s : State n Command) (r : Fin n) (p : Proposal Command)
      (message : p ∈ s.commits) (alive : (s.nodes r).alive = true)
      (normal : (s.nodes r).changing = false) (view : (s.nodes r).promise = p.view)
      (present : p.log <+: (s.nodes r).accepted.log)
      (index : (s.nodes r).applied.log.length ≤ p.log.length) :
      Step primary s (updateNode s r { s.nodes r with applied := ⟨p.view, p.log⟩ })
  | crash (s : State n Command) (r : Fin n) :
      Step primary s (updateNode s r { s.nodes r with alive := false })
  | recover (s : State n Command) (r : Fin n) :
      Step primary s (updateNode s r { s.nodes r with alive := true })

inductive Reachable (primary : Nat → Fin n) : State n Command → Prop where
  | init : Reachable primary initial
  | next : Reachable primary s → Step primary s t → Reachable primary t

structure LocalInvariant (primary : Nat → Fin n) (s : State n Command) : Prop where
  history : HistoryFacts s
  accepted_proposed : ∀ r, Proposal.mk (s.nodes r).accepted.view (s.nodes r).accepted.log ∈ s.proposals
  accepted_le_promise : ∀ r, (s.nodes r).accepted.view ≤ (s.nodes r).promise
  normal_view : ∀ r, (s.nodes r).changing = false → (s.nodes r).accepted.view = (s.nodes r).promise
  changing_view : ∀ r, (s.nodes r).changing = true → (s.nodes r).accepted.view < (s.nodes r).promise
  primary_latest : ∀ p ∈ s.proposals,
    p.view ≤ (s.nodes (primary p.view)).accepted.view ∧
      (p.view = (s.nodes (primary p.view)).accepted.view → p.log <+: (s.nodes (primary p.view)).accepted.log)
  votes_current : ∀ v ∈ s.votes,
    v.view ≤ (s.nodes v.sender).accepted.view ∧
      (v.view = (s.nodes v.sender).accepted.view → v.log <+: (s.nodes v.sender).accepted.log)
  reports_promised : ∀ r ∈ s.reports, r.view ≤ (s.nodes r.sender).promise
  commits_certified : ∀ p ∈ s.commits, Certified s p.view p.log
  applied_certified : ∀ r, (s.nodes r).applied.log = [] ∨
    Proposal.mk (s.nodes r).applied.view (s.nodes r).applied.log ∈ s.commits
  applied_view : ∀ r, (s.nodes r).applied.view ≤ (s.nodes r).accepted.view

end Aegis
