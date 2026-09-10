import Aegis.Protocol
import Aegis.NodeInvariants
import Aegis.MessageInvariants
namespace Aegis
set_option maxHeartbeats 600000

private theorem proposal_below_primary {primary : Nat → Fin n} {s : State n Command}
    (h : LocalInvariant primary s) (v : Nat)
    (hv : (s.nodes (primary v)).accepted.view = v) :
    ∀ p ∈ s.proposals, p.view = v → p.log <+: (s.nodes (primary v)).accepted.log := by
  intro p hp he
  have latest := (h.primary_latest p hp).2
  have same : p.view = (s.nodes (primary p.view)).accepted.view := by
    rw [he, hv]
  simpa [he] using latest same

theorem propose_history {primary : Nat → Fin n} {s : State n Command}
    (h : LocalInvariant primary s) (v : Nat) (command : Command)
    (normal : (s.nodes (primary v)).changing = false)
    (view : (s.nodes (primary v)).promise = v) :
    HistoryFacts
      { updateNode s (primary v)
          { s.nodes (primary v) with
            accepted := ⟨v, (s.nodes (primary v)).accepted.log ++ [command]⟩ }
        with proposals := ⟨v, (s.nodes (primary v)).accepted.log ++ [command]⟩ :: s.proposals } := by
  have av : (s.nodes (primary v)).accepted.view = v :=
    (h.normal_view (primary v) normal).trans view
  have old_below := proposal_below_primary h v av
  constructor
  · intro p hp
    rcases List.mem_cons.mp hp with hp | hp
    · subst p
      by_cases zero : v = 0
      · exact Or.inl zero
      · have ho := h.history.proposals_origin
          ⟨(s.nodes (primary v)).accepted.view, (s.nodes (primary v)).accepted.log⟩
          (h.accepted_proposed (primary v))
        rcases ho with hz | ⟨e, he, hv, pref⟩
        · exact (zero (av ▸ hz)).elim
        · exact Or.inr ⟨e, he, hv.trans av, pref.trans (List.prefix_append _ _)⟩
    · exact h.history.proposals_origin p hp
  · intro p hp q hq same
    rcases List.mem_cons.mp hp with hp | hp
    · subst p
      rcases List.mem_cons.mp hq with hq | hq
      · subst q
        exact Or.inl List.prefix_rfl
      · exact Or.inr ((old_below q hq same.symm).trans (List.prefix_append _ _))
    · rcases List.mem_cons.mp hq with hq | hq
      · subst q
        exact Or.inl ((old_below p hp same).trans (List.prefix_append _ _))
      · exact h.history.proposals_comparable p hp q hq same
  · intro vote hv
    exact List.mem_cons_of_mem _ (h.history.votes_proposed vote hv)
  · intro report hr
    exact List.mem_cons_of_mem _ (h.history.reports_proposed report hr)
  · exact h.history.reports_lower
  · exact h.history.reports_fence
  · exact h.history.elections_valid
  · intro election he
    exact List.mem_cons_of_mem _ (h.history.elections_proposed election he)

theorem elect_history {primary : Nat → Fin n} {s : State n Command}
    (h : LocalInvariant primary s) (e : Election n Command)
    (changing : (s.nodes (primary e.view)).changing = true)
    (view : (s.nodes (primary e.view)).promise = e.view)
    (certificate : ElectionValid s e) :
    HistoryFacts
      { updateNode s (primary e.view)
          { s.nodes (primary e.view) with accepted := ⟨e.view, e.base⟩, changing := false }
        with proposals := ⟨e.view, e.base⟩ :: s.proposals, elections := e :: s.elections } := by
  have fresh : ∀ p ∈ s.proposals, p.view ≠ e.view := by
    intro p hp same
    have lower := h.changing_view (primary e.view) changing
    have latest := (h.primary_latest p hp).1
    rw [same] at latest
    rw [view] at lower
    omega
  constructor
  · intro p hp
    rcases List.mem_cons.mp hp with hp | hp
    · subst p
      exact Or.inr ⟨e, List.mem_cons_self, rfl, List.prefix_rfl⟩
    · rcases h.history.proposals_origin p hp with zero | ⟨f, hf, same, pref⟩
      · exact Or.inl zero
      · exact Or.inr ⟨f, List.mem_cons_of_mem _ hf, same, pref⟩
  · intro p hp q hq same
    rcases List.mem_cons.mp hp with hp | hp
    · subst p
      rcases List.mem_cons.mp hq with hq | hq
      · subst q
        exact Or.inl List.prefix_rfl
      · exact (fresh q hq same.symm).elim
    · rcases List.mem_cons.mp hq with hq | hq
      · subst q
        exact (fresh p hp same).elim
      · exact h.history.proposals_comparable p hp q hq same
  · intro vote hv
    exact List.mem_cons_of_mem _ (h.history.votes_proposed vote hv)
  · intro report hr
    exact List.mem_cons_of_mem _ (h.history.reports_proposed report hr)
  · exact h.history.reports_lower
  · exact h.history.reports_fence
  · intro f hf
    rcases List.mem_cons.mp hf with hf | hf
    · subst f
      exact certificate
    · exact h.history.elections_valid f hf
  · intro f hf
    rcases List.mem_cons.mp hf with hf | hf
    · subst f
      exact List.mem_cons_self
    · exact List.mem_cons_of_mem _ (h.history.elections_proposed f hf)

private def publish_state (s : State n Command) (primary : Nat → Fin n)
    (v : Nat) (log : Log Command) (elections : List (Election n Command)) : State n Command :=
  { updateNode s (primary v)
      { s.nodes (primary v) with accepted := ⟨v, log⟩, changing := false }
    with proposals := ⟨v, log⟩ :: s.proposals, elections := elections }

private theorem publication_progress_trans {a b c : Snapshot Command}
    (ab : a.view ≤ b.view ∧ (a.view = b.view → a.log <+: b.log))
    (bc : b.view ≤ c.view ∧ (b.view = c.view → b.log <+: c.log)) :
    a.view ≤ c.view ∧ (a.view = c.view → a.log <+: c.log) := by
  constructor
  · exact Nat.le_trans ab.1 bc.1
  · intro equal
    have left : a.view = b.view := by omega
    have right : b.view = c.view := by omega
    exact (ab.2 left).trans (bc.2 right)

private theorem publish_primary_preserves
    {primary : Nat → Fin n} {s : State n Command}
    (h : LocalInvariant primary s) (v : Nat) (log : Log Command)
    (elections : List (Election n Command))
    (view : (s.nodes (primary v)).promise = v)
    (progress : (s.nodes (primary v)).accepted.view ≤ v ∧
      ((s.nodes (primary v)).accepted.view = v →
        (s.nodes (primary v)).accepted.log <+: log))
    (history : HistoryFacts (publish_state s primary v log elections)) :
    LocalInvariant primary (publish_state s primary v log elections) := by
  constructor
  · exact history
  · intro r
    by_cases eq : r = primary v
    · simp [publish_state, updateNode, eq]
    · simpa [publish_state, updateNode, eq] using
        List.mem_cons_of_mem (Proposal.mk v log) (h.accepted_proposed r)
  · intro r
    by_cases eq : r = primary v
    · simp [publish_state, updateNode, eq, view]
    · simpa [publish_state, updateNode, eq] using h.accepted_le_promise r
  · intro r
    by_cases eq : r = primary v
    · simp [publish_state, updateNode, eq, view]
    · simpa [publish_state, updateNode, eq] using h.normal_view r
  · intro r
    by_cases eq : r = primary v
    · simp [publish_state, updateNode, eq]
    · simpa [publish_state, updateNode, eq] using h.changing_view r
  · intro p member
    rcases List.mem_cons.mp member with equal | member
    · subst p
      simp [publish_state, updateNode]
    · have old := h.primary_latest p member
      by_cases eq : primary p.view = primary v
      · rw [eq] at old
        simpa [publish_state, updateNode, eq] using
          publication_progress_trans (a := ⟨p.view, p.log⟩)
            (c := ⟨v, log⟩) old progress
      · simpa [publish_state, updateNode, eq] using old
  · intro vote member
    have old := h.votes_current vote member
    by_cases eq : vote.sender = primary v
    · rw [eq] at old
      simpa [publish_state, updateNode, eq] using
        publication_progress_trans (a := ⟨vote.view, vote.log⟩)
          (c := ⟨v, log⟩) old progress
    · simpa [publish_state, updateNode, eq] using old
  · intro report member
    have old := h.reports_promised report member
    by_cases eq : report.sender = primary v
    · simpa [publish_state, updateNode, eq] using old
    · simpa [publish_state, updateNode, eq] using old
  · exact h.commits_certified
  · intro r
    by_cases eq : r = primary v
    · simpa [publish_state, updateNode, eq] using h.applied_certified r
    · simpa [publish_state, updateNode, eq] using h.applied_certified r
  · intro r
    by_cases eq : r = primary v
    · simpa [publish_state, updateNode, eq] using
        Nat.le_trans (h.applied_view (primary v)) progress.1
    · simpa [publish_state, updateNode, eq] using h.applied_view r

theorem propose_preserves {primary : Nat → Fin n} {s : State n Command}
    (h : LocalInvariant primary s) (v : Nat) (command : Command)
    (normal : (s.nodes (primary v)).changing = false)
    (view : (s.nodes (primary v)).promise = v) :
    LocalInvariant primary
      { updateNode s (primary v)
          { s.nodes (primary v) with
            accepted := ⟨v, (s.nodes (primary v)).accepted.log ++ [command]⟩ }
        with proposals := ⟨v, (s.nodes (primary v)).accepted.log ++ [command]⟩ :: s.proposals } := by
  have av : (s.nodes (primary v)).accepted.view = v :=
    (h.normal_view (primary v) normal).trans view
  have history := propose_history h v command normal view
  have prepared : HistoryFacts
      (publish_state s primary v ((s.nodes (primary v)).accepted.log ++ [command]) s.elections) := by
    simpa [publish_state, normal] using history
  have result := publish_primary_preserves h v
    ((s.nodes (primary v)).accepted.log ++ [command]) s.elections view
    ⟨Nat.le_of_eq av, fun _ => List.prefix_append _ _⟩ prepared
  simpa [publish_state, normal] using result

theorem elect_preserves {primary : Nat → Fin n} {s : State n Command}
    (h : LocalInvariant primary s) (e : Election n Command)
    (changing : (s.nodes (primary e.view)).changing = true)
    (view : (s.nodes (primary e.view)).promise = e.view)
    (certificate : ElectionValid s e) :
    LocalInvariant primary
      { updateNode s (primary e.view)
          { s.nodes (primary e.view) with accepted := ⟨e.view, e.base⟩, changing := false }
        with proposals := ⟨e.view, e.base⟩ :: s.proposals, elections := e :: s.elections } := by
  have lower := h.changing_view (primary e.view) changing
  rw [view] at lower
  exact publish_primary_preserves h e.view e.base (e :: s.elections) view
    ⟨Nat.le_of_lt lower, fun equality => (Nat.ne_of_lt lower equality).elim⟩
    (elect_history h e changing view certificate)

theorem initial_invariant (primary : Nat → Fin n) :
    LocalInvariant primary (initial : State n Command) := by
  constructor
  · constructor <;> simp [initial]
  all_goals simp [initial]

theorem invariant_preserved {primary : Nat → Fin n}
    {s t : State n Command}
    (h : LocalInvariant primary s) (step : Step primary s t) :
    LocalInvariant primary t := by
  cases step with
  | beginView r v _ newer => exact beginView_preserves h r v newer
  | sendReport r _ changing => exact sendReport_preserves r h changing
  | propose v command _ normal view => exact propose_preserves h v command normal view
  | elect e _ changing view certificate => exact elect_preserves h e changing view certificate
  | accept r p message _ _ view extend => exact accept_preserves h r p message view extend
  | acknowledge r _ normal => exact acknowledge_preserves r h normal
  | commit p _ _ _ _ certificate => exact commit_preserves p h certificate
  | applyCommit r p message _ normal view _ _ => exact applyCommit_preserves h r p message normal view
  | crash r => exact alive_preserves h r false
  | recover r => exact alive_preserves h r true

theorem reachable_invariant {primary : Nat → Fin n}
    {s : State n Command} (h : Reachable primary s) :
    LocalInvariant primary s := by
  induction h with
  | init => exact initial_invariant primary
  | next _ step induction => exact invariant_preserved induction step

end Aegis
