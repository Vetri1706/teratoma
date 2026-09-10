import Std

namespace Aegis

def Q (n : Nat) := Fin n → Bool

def count : {n : Nat} → Q n → Nat
  | 0, _ => 0
  | n + 1, q => (if q 0 then 1 else 0) + count (fun i : Fin n => q i.succ)

def Majority {n : Nat} (q : Q n) : Prop := n < 2 * count q

theorem count_le {n : Nat} (q : Q n) : count q ≤ n := by
  induction n with
  | zero => simp [count]
  | succ n ih =>
      have tail_bound := ih (fun i : Fin n => q i.succ)
      cases head : q 0 <;> simp [count, head] <;> omega

theorem intersection_of_count_sum_gt {n : Nat} (p q : Q n)
    (h : n < count p + count q) :
    ∃ r : Fin n, p r = true ∧ q r = true := by
  induction n with
  | zero => simp [count] at h
  | succ n ih =>
      let pt : Q n := fun i => p i.succ
      let qt : Q n := fun i => q i.succ
      by_cases both : p 0 = true ∧ q 0 = true
      · exact ⟨0, both⟩
      · have tail_sum : n < count pt + count qt := by
          cases hp : p 0 <;> cases hq : q 0 <;>
            simp [count, hp, hq, pt, qt] at h both ⊢ <;> omega
        obtain ⟨r, hp, hq⟩ := ih pt qt tail_sum
        exact ⟨r.succ, hp, hq⟩

theorem majority_intersection {n : Nat} {p q : Q n}
    (hp : Majority p) (hq : Majority q) :
    ∃ r : Fin n, p r = true ∧ q r = true := by
  unfold Majority at hp hq
  apply intersection_of_count_sum_gt p q
  omega

end Aegis
