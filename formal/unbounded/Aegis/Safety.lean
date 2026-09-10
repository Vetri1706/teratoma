import Aegis.LocalInvariants
import Aegis.ViewChange
import Aegis.NodeFacts

namespace Aegis

variable {n : Nat} {Command : Type} {primary : Nat → Fin n}
variable {s t : State n Command} {run : Nat → State n Command}

def Agreement (s : State n Command) : Prop :=
  ∀ r q : Fin n, ∀ i : Nat,
    ∀ hr : i < (s.nodes r).applied.log.length,
    ∀ hq : i < (s.nodes q).applied.log.length,
    (s.nodes r).applied.log[i] = (s.nodes q).applied.log[i]

def LeaderComplete (s : State n Command) : Prop :=
  ∀ p ∈ s.commits, ∀ e ∈ s.elections, p.view < e.view → p.log <+: e.base

theorem applied_compatible (inv : LocalInvariant primary s) (r q : Fin n) :
    (s.nodes r).applied.log <+: (s.nodes q).applied.log ∨
    (s.nodes q).applied.log <+: (s.nodes r).applied.log := by
  rcases inv.applied_certified r with empty | certified
  · left
    rw [empty]
    exact List.nil_prefix
  · rcases inv.applied_certified q with empty | certifiedQ
    · right
      rw [empty]
      exact List.nil_prefix
    · exact certificates_compatible inv.history
        (inv.commits_certified _ certified) (inv.commits_certified _ certifiedQ)

theorem invariant_agreement (inv : LocalInvariant primary s) : Agreement s := by
  intro r q i hr hq
  rcases applied_compatible inv r q with forward | backward
  · exact forward.getElem hr
  · exact (backward.getElem hq).symm

theorem reachable_agreement (reachable : Reachable primary s) : Agreement s :=
  invariant_agreement (reachable_invariant reachable)

theorem reachable_leader_completeness (reachable : Reachable primary s) : LeaderComplete s := by
  have inv := reachable_invariant reachable
  intro p committed e elected later
  exact leaderCompleteness inv.history (inv.commits_certified p committed)
    (inv.history.elections_proposed e elected) later

theorem step_commits_persist (transition : Step primary s t) : s.commits ⊆ t.commits := by
  intro p member
  cases transition <;> simp_all [updateNode]

theorem step_applied_prefix (inv : LocalInvariant primary s) (transition : Step primary s t)
    (r : Fin n) : (s.nodes r).applied.log <+: (t.nodes r).applied.log := by
  have nextInv := invariant_preserved inv transition
  have lengths := step_applied_indices_monotone transition r
  rcases inv.applied_certified r with empty | committed
  · rw [empty]
    exact List.nil_prefix
  · rcases nextInv.applied_certified r with empty | committedNext
    · rw [empty] at lengths ⊢
      have zero : (s.nodes r).applied.log = [] := List.length_eq_zero_iff.mp (Nat.eq_zero_of_le_zero lengths)
      rw [zero]
      exact List.prefix_refl _
    · have compatible := certificates_compatible nextInv.history
        (nextInv.commits_certified _ (step_commits_persist transition committed))
        (nextInv.commits_certified _ committedNext)
      rcases compatible with forward | backward
      · exact forward
      · have equal := backward.eq_of_length_le lengths
        change (t.nodes r).applied.log = (s.nodes r).applied.log at equal
        rw [equal]
        exact List.prefix_refl _


def Execution (primary : Nat → Fin n) (run : Nat → State n Command) : Prop :=
  run 0 = initial ∧ ∀ time, Step primary (run time) (run (time + 1))

theorem execution_reachable (execution : Execution primary run) (time : Nat) :
    Reachable primary (run time) := by
  induction time with
  | zero => rw [execution.1]; exact Reachable.init
  | succ time ih => exact Reachable.next ih (execution.2 time)

theorem infinite_execution_agreement (execution : Execution primary run) :
    ∀ time, Agreement (run time) := by
  intro time
  exact reachable_agreement (execution_reachable execution time)

theorem infinite_execution_leader_completeness (execution : Execution primary run) :
    ∀ time, LeaderComplete (run time) := by
  intro time
  exact reachable_leader_completeness (execution_reachable execution time)

theorem execution_promises_monotone (execution : Execution primary run)
    (start stop : Nat) (order : start ≤ stop) (r : Fin n) :
    ((run start).nodes r).promise ≤ ((run stop).nodes r).promise := by
  induction order with
  | refl => exact Nat.le_refl _
  | @step next order ih => exact Nat.le_trans ih (step_promises_monotone (execution.2 next) r)

theorem execution_accepted_views_monotone (execution : Execution primary run)
    (start stop : Nat) (order : start ≤ stop) (r : Fin n) :
    ((run start).nodes r).accepted.view ≤ ((run stop).nodes r).accepted.view := by
  induction order with
  | refl => exact Nat.le_refl _
  | @step next order ih =>
    exact Nat.le_trans ih
      ((accepted_progress (reachable_invariant (execution_reachable execution next))
        (execution.2 next) r).1)

theorem execution_applied_indices_monotone (execution : Execution primary run)
    (start stop : Nat) (order : start ≤ stop) (r : Fin n) :
    ((run start).nodes r).applied.log.length ≤ ((run stop).nodes r).applied.log.length := by
  induction order with
  | refl => exact Nat.le_refl _
  | @step next order ih => exact Nat.le_trans ih (step_applied_indices_monotone (execution.2 next) r)

theorem execution_commits_persist (execution : Execution primary run)
    (start stop : Nat) (order : start ≤ stop) :
    (run start).commits ⊆ (run stop).commits := by
  induction order with
  | refl => exact List.Subset.refl _
  | @step next order ih => exact List.Subset.trans ih (step_commits_persist (execution.2 next))

theorem execution_applied_prefix (execution : Execution primary run)
    (start stop : Nat) (order : start ≤ stop) (r : Fin n) :
    ((run start).nodes r).applied.log <+: ((run stop).nodes r).applied.log := by
  induction order with
  | refl => exact List.prefix_refl _
  | @step next order ih =>
    exact ih.trans
      (step_applied_prefix (reachable_invariant (execution_reachable execution next))
        (execution.2 next) r)

theorem cross_time_agreement (execution : Execution primary run)
    (start stop : Nat) (order : start ≤ stop) (r q : Fin n) (i : Nat)
    (hr : i < ((run start).nodes r).applied.log.length)
    (hq : i < ((run stop).nodes q).applied.log.length) :
    ((run start).nodes r).applied.log[i] = ((run stop).nodes q).applied.log[i] := by
  have preserved := execution_applied_prefix execution start stop order r
  have present := Nat.lt_of_lt_of_le hr preserved.length_le
  exact (preserved.getElem hr).trans
    (infinite_execution_agreement execution stop r q i present hq)

theorem all_time_agreement (execution : Execution primary run)
    (first second : Nat) (r q : Fin n) (i : Nat)
    (hr : i < ((run first).nodes r).applied.log.length)
    (hq : i < ((run second).nodes q).applied.log.length) :
    ((run first).nodes r).applied.log[i] = ((run second).nodes q).applied.log[i] := by
  by_cases order : first ≤ second
  · exact cross_time_agreement execution first second order r q i hr hq
  · exact (cross_time_agreement execution second first (Nat.le_of_not_ge order) q r i hq hr).symm

theorem past_commit_in_future_leader (execution : Execution primary run)
    (start stop : Nat) (order : start ≤ stop)
    (p : Proposal Command) (committed : p ∈ (run start).commits)
    (e : Election n Command) (elected : e ∈ (run stop).elections)
    (laterView : p.view < e.view) : p.log <+: e.base := by
  exact infinite_execution_leader_completeness execution stop p
    (execution_commits_persist execution start stop order committed) e elected laterView

end Aegis
