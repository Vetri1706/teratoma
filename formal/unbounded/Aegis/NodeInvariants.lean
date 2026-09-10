import Aegis.Protocol

namespace Aegis

private theorem progress_trans {a b c : Snapshot Command}
    (ab : a.view ≤ b.view ∧ (a.view = b.view → a.log <+: b.log))
    (bc : b.view ≤ c.view ∧ (b.view = c.view → b.log <+: c.log)) :
    a.view ≤ c.view ∧ (a.view = c.view → a.log <+: c.log) := by
  constructor
  · exact Nat.le_trans ab.1 bc.1
  · intro equal
    have left : a.view = b.view := by omega
    have right : b.view = c.view := by omega
    exact (ab.2 left).trans (bc.2 right)

private theorem node_update_preserves {primary : Nat → Fin n}
    {s : State n Command} (h : LocalInvariant primary s) (r : Fin n) (node : Node Command)
    (promise : (s.nodes r).promise ≤ node.promise)
    (progress : (s.nodes r).accepted.view ≤ node.accepted.view ∧
      ((s.nodes r).accepted.view = node.accepted.view → (s.nodes r).accepted.log <+: node.accepted.log))
    (proposed : Proposal.mk node.accepted.view node.accepted.log ∈ s.proposals)
    (bound : node.accepted.view ≤ node.promise)
    (normal : node.changing = false → node.accepted.view = node.promise)
    (changing : node.changing = true → node.accepted.view < node.promise)
    (applied : node.applied.log = [] ∨ Proposal.mk node.applied.view node.applied.log ∈ s.commits)
    (applied_view : node.applied.view ≤ node.accepted.view) :
    LocalInvariant primary (updateNode s r node) := by
  constructor
  · exact {
      proposals_origin := h.history.proposals_origin
      proposals_comparable := h.history.proposals_comparable
      votes_proposed := h.history.votes_proposed
      reports_proposed := h.history.reports_proposed
      reports_lower := h.history.reports_lower
      reports_fence := h.history.reports_fence
      elections_valid := h.history.elections_valid
      elections_proposed := h.history.elections_proposed }
  · intro i
    by_cases eq : i = r
    · simpa [updateNode, eq] using proposed
    · simpa [updateNode, eq] using h.accepted_proposed i
  · intro i
    by_cases eq : i = r
    · simpa [updateNode, eq] using bound
    · simpa [updateNode, eq] using h.accepted_le_promise i
  · intro i
    by_cases eq : i = r
    · simpa [updateNode, eq] using normal
    · simpa [updateNode, eq] using h.normal_view i
  · intro i
    by_cases eq : i = r
    · simpa [updateNode, eq] using changing
    · simpa [updateNode, eq] using h.changing_view i
  · intro p member
    have old := h.primary_latest p member
    by_cases eq : primary p.view = r
    · rw [eq] at old
      simpa [updateNode, eq] using progress_trans (a := ⟨p.view, p.log⟩) old progress
    · simpa [updateNode, eq] using old
  · intro v member
    have old := h.votes_current v member
    by_cases eq : v.sender = r
    · rw [eq] at old
      simpa [updateNode, eq] using progress_trans (a := ⟨v.view, v.log⟩) old progress
    · simpa [updateNode, eq] using old
  · intro report member
    have old := h.reports_promised report member
    by_cases eq : report.sender = r
    · rw [eq] at old
      simpa [updateNode, eq] using Nat.le_trans old promise
    · simpa [updateNode, eq] using old
  · exact h.commits_certified
  · intro i
    by_cases eq : i = r
    · simpa [updateNode, eq] using applied
    · simpa [updateNode, eq] using h.applied_certified i
  · intro i
    by_cases eq : i = r
    · simpa [updateNode, eq] using applied_view
    · simpa [updateNode, eq] using h.applied_view i

theorem beginView_preserves {primary : Nat → Fin n} {s : State n Command}
    (h : LocalInvariant primary s) (r : Fin n) (v : Nat)
    (newer : (s.nodes r).promise < v) :
    LocalInvariant primary (updateNode s r {s.nodes r with promise := v, changing := true}) := by
  apply node_update_preserves h r
  · exact Nat.le_of_lt newer
  · exact ⟨Nat.le_refl _, fun _ => List.prefix_refl _⟩
  · exact h.accepted_proposed r
  · exact Nat.le_trans (h.accepted_le_promise r) (Nat.le_of_lt newer)
  · simp
  · intro _
    exact Nat.lt_of_le_of_lt (h.accepted_le_promise r) newer
  · exact h.applied_certified r
  · exact h.applied_view r

theorem accept_preserves {primary : Nat → Fin n} {s : State n Command}
    (h : LocalInvariant primary s) (r : Fin n) (p : Proposal Command)
    (message : p ∈ s.proposals) (view : (s.nodes r).promise ≤ p.view)
    (extend : (s.nodes r).accepted.view = p.view → (s.nodes r).accepted.log <+: p.log) :
    LocalInvariant primary (updateNode s r
      {s.nodes r with promise := p.view, accepted := ⟨p.view, p.log⟩, changing := false}) := by
  have progress := Nat.le_trans (h.accepted_le_promise r) view
  apply node_update_preserves h r
  · exact view
  · exact ⟨progress, extend⟩
  · exact message
  · exact Nat.le_refl _
  · intro _; rfl
  · simp
  · exact h.applied_certified r
  · exact Nat.le_trans (h.applied_view r) progress

theorem applyCommit_preserves {primary : Nat → Fin n} {s : State n Command}
    (h : LocalInvariant primary s) (r : Fin n) (p : Proposal Command)
    (message : p ∈ s.commits)
    (normal : (s.nodes r).changing = false) (view : (s.nodes r).promise = p.view) :
    LocalInvariant primary (updateNode s r {s.nodes r with applied := ⟨p.view, p.log⟩}) := by
  apply node_update_preserves h r
  · exact Nat.le_refl _
  · exact ⟨Nat.le_refl _, fun _ => List.prefix_refl _⟩
  · exact h.accepted_proposed r
  · exact h.accepted_le_promise r
  · exact h.normal_view r
  · exact h.changing_view r
  · exact Or.inr message
  · have equal := h.normal_view r normal
    simp only [view] at equal
    exact Nat.le_of_eq equal.symm

theorem alive_preserves {primary : Nat → Fin n} {s : State n Command}
    (h : LocalInvariant primary s) (r : Fin n) (alive : Bool) :
    LocalInvariant primary (updateNode s r {s.nodes r with alive := alive}) := by
  apply node_update_preserves h r
  · exact Nat.le_refl _
  · exact ⟨Nat.le_refl _, fun _ => List.prefix_refl _⟩
  · exact h.accepted_proposed r
  · exact h.accepted_le_promise r
  · exact h.normal_view r
  · exact h.changing_view r
  · exact h.applied_certified r
  · exact h.applied_view r

end Aegis
