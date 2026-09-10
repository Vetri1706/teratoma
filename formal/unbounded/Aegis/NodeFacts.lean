import Aegis.Protocol

namespace Aegis

theorem step_promises_monotone (transition : Step primary s t) :
    ∀ r, (s.nodes r).promise ≤ (t.nodes r).promise := by
  cases transition <;> intro i <;> simp only [updateNode]
  all_goals first | exact Nat.le_refl _ | split <;> simp_all <;> omega

theorem step_applied_indices_monotone (transition : Step primary s t) :
    ∀ r, (s.nodes r).applied.log.length ≤ (t.nodes r).applied.log.length := by
  cases transition <;> intro i <;> simp only [updateNode]
  all_goals first | exact Nat.le_refl _ | split <;> simp_all

theorem accepted_progress (inv : LocalInvariant primary s)
    (transition : Step primary s t) :
    ∀ r, (s.nodes r).accepted.view ≤ (t.nodes r).accepted.view ∧
      ((s.nodes r).accepted.view = (t.nodes r).accepted.view →
        (s.nodes r).accepted.log <+: (t.nodes r).accepted.log) := by
  cases transition with
  | propose v command alive normal view =>
      have old_view : (s.nodes (primary v)).accepted.view = v :=
        (inv.normal_view (primary v) normal).trans view
      intro i
      simp only [updateNode]
      split
      · rename_i equal
        subst i
        exact ⟨Nat.le_of_eq old_view, fun _ => ⟨[command], rfl⟩⟩
      · exact ⟨Nat.le_refl _, fun _ => List.prefix_refl _⟩
  | elect e alive changing view certificate =>
      have old_view_lt : (s.nodes (primary e.view)).accepted.view < e.view := by
        calc
          (s.nodes (primary e.view)).accepted.view <
              (s.nodes (primary e.view)).promise := inv.changing_view (primary e.view) changing
          _ = e.view := view
      intro i
      simp only [updateNode]
      split
      · rename_i equal
        subst i
        refine ⟨Nat.le_of_lt old_view_lt, ?_⟩
        intro equal
        exact False.elim ((Nat.ne_of_lt old_view_lt) equal)
      · exact ⟨Nat.le_refl _, fun _ => List.prefix_refl _⟩
  | accept r p message backup alive view extendSameView =>
      intro i
      simp only [updateNode]
      split
      · rename_i equal
        subst i
        exact ⟨Nat.le_trans (inv.accepted_le_promise r) view, extendSameView⟩
      · exact ⟨Nat.le_refl _, fun _ => List.prefix_refl _⟩
  | _ =>
      intro i
      simp only [updateNode]
      first
        | exact ⟨Nat.le_refl _, fun _ => List.prefix_refl _⟩
        | split
          · rename_i equal
            subst i
            exact ⟨Nat.le_refl _, fun _ => List.prefix_refl _⟩
          · exact ⟨Nat.le_refl _, fun _ => List.prefix_refl _⟩

theorem snapshot_progress_trans {a b c : Snapshot Command}
    (hab : a.view ≤ b.view ∧ (a.view = b.view → a.log <+: b.log))
    (hbc : b.view ≤ c.view ∧ (b.view = c.view → b.log <+: c.log)) :
    a.view ≤ c.view ∧ (a.view = c.view → a.log <+: c.log) := by
  rcases hab with ⟨hab, hpab⟩
  rcases hbc with ⟨hbc, hpbc⟩
  constructor
  · exact Nat.le_trans hab hbc
  · intro hac
    exact (hpab (by omega)).trans (hpbc (by omega))

end Aegis
