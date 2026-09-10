import Aegis.Protocol

namespace Aegis

theorem nonempty_applied_trace {Command : Type} (command : Command) :
    ∃ s : State 1 Command, Reachable (fun _ => 0) s ∧
      (s.nodes 0).applied.log = [command] := by
  let r : Fin 1 := 0
  let primary : Nat → Fin 1 := fun _ => r
  let s0 : State 1 Command := initial
  let p : Proposal Command := ⟨0, [command]⟩
  let s1 : State 1 Command :=
    { updateNode s0 r { s0.nodes r with accepted := ⟨0, [command]⟩ }
      with proposals := p :: s0.proposals }
  let s2 : State 1 Command :=
    { s1 with votes := ⟨r, 0, [command]⟩ :: s1.votes }
  let s3 : State 1 Command := { s2 with commits := p :: s2.commits }
  let s4 : State 1 Command :=
    updateNode s3 r { s3.nodes r with applied := ⟨0, [command]⟩ }
  have h1 : Step primary s0 s1 := Step.propose s0 0 command rfl rfl rfl
  have h2 : Step primary s1 s2 := Step.acknowledge s1 r rfl rfl
  have cert : Certified s2 0 [command] := by
    refine ⟨(fun _ => true), ?_, ?_⟩
    · simp [Majority, count]
    · intro i _
      have hi : i = r := Subsingleton.elim _ _
      subst i
      exact ⟨[command], by simp [s2], List.prefix_refl _⟩
  have h3 : Step primary s2 s3 :=
    Step.commit s2 p rfl rfl rfl (by simp [s2, s1]) cert
  have h4 : Step primary s3 s4 :=
    Step.applyCommit s3 r p (by simp [s3]) rfl rfl rfl
      (List.prefix_refl _) (by simp [s3, s2, s1, s0, initial, updateNode, p])
  refine ⟨s4, ?_, rfl⟩
  exact Reachable.next (Reachable.next (Reachable.next
    (Reachable.next Reachable.init h1) h2) h3) h4

theorem three_replica_election_trace {Command : Type} (command : Command) :
    ∃ (primary : Nat → Fin 3) (s : State 3 Command),
      Reachable primary s ∧
      Proposal.mk 0 [command] ∈ s.commits ∧
      (s.nodes 0).applied.log = [command] ∧
      (s.nodes 1).accepted.view = 1 ∧
      (s.nodes 1).accepted.log = [command] ∧
      ∃ e ∈ s.elections, e.view = 1 ∧ e.base = [command] := by
  let primary : Nat → Fin 3 := fun v => if v = 0 then 0 else 1
  let s0 : State 3 Command := initial
  let p : Proposal Command := ⟨0, [command]⟩
  let snap : Snapshot Command := ⟨0, [command]⟩
  let s1 : State 3 Command :=
    { updateNode s0 0 { s0.nodes 0 with accepted := snap }
      with proposals := p :: s0.proposals }
  let s2 : State 3 Command :=
    updateNode s1 1 { s1.nodes 1 with promise := 0, accepted := snap, changing := false }
  let s3 : State 3 Command :=
    { s2 with votes := ⟨0, 0, [command]⟩ :: s2.votes }
  let s4 : State 3 Command :=
    { s3 with votes := ⟨1, 0, [command]⟩ :: s3.votes }
  let s5 : State 3 Command := { s4 with commits := p :: s4.commits }
  let s6 : State 3 Command :=
    updateNode s5 0 { s5.nodes 0 with applied := snap }
  let s7 : State 3 Command :=
    updateNode s6 0 { s6.nodes 0 with promise := 1, changing := true }
  let s8 : State 3 Command :=
    updateNode s7 1 { s7.nodes 1 with promise := 1, changing := true }
  let s9 : State 3 Command :=
    { s8 with reports := ⟨0, 1, snap⟩ :: s8.reports }
  let s10 : State 3 Command :=
    { s9 with reports := ⟨1, 1, snap⟩ :: s9.reports }
  let mask : Q 3 := fun i => decide (i = 0 ∨ i = 1)
  let e : Election 3 Command := ⟨1, mask, (fun _ => snap), 0⟩
  let s11 : State 3 Command :=
    { updateNode s10 1 { s10.nodes 1 with accepted := ⟨1, e.base⟩, changing := false }
      with proposals := ⟨1, e.base⟩ :: s10.proposals, elections := e :: s10.elections }
  have mask_majority : Majority mask := by
    unfold Majority
    decide
  have h1 : Step primary s0 s1 := Step.propose s0 0 command rfl rfl rfl
  have h2 : Step primary s1 s2 :=
    Step.accept s1 1 p (by simp [s1]) (by change (1 : Fin 3) ≠ 0; decide) rfl
      (Nat.le_refl _) (fun _ => List.nil_prefix)
  have h3 : Step primary s2 s3 := Step.acknowledge s2 0 rfl rfl
  have h4 : Step primary s3 s4 := Step.acknowledge s3 1 rfl rfl
  have cert : Certified s4 0 [command] := by
    refine ⟨mask, mask_majority, ?_⟩
    intro i hi
    have cases_i : i = 0 ∨ i = 1 := by simpa [mask] using hi
    rcases cases_i with rfl | rfl <;>
      exact ⟨[command], by simp [s4, s3], List.prefix_refl _⟩
  have h5 : Step primary s4 s5 :=
    Step.commit s4 p rfl rfl rfl (by simp [s4, s3, s2, s1, updateNode]) cert
  have h6 : Step primary s5 s6 :=
    Step.applyCommit s5 0 p (by simp [s5]) rfl rfl rfl
      (List.prefix_refl _) (by change 0 ≤ 1; omega)
  have h7 : Step primary s6 s7 := Step.beginView s6 0 1 rfl (by change 0 < 1; omega)
  have h8 : Step primary s7 s8 := Step.beginView s7 1 1 rfl (by change 0 < 1; omega)
  have h9 : Step primary s8 s9 := Step.sendReport s8 0 rfl rfl
  have h10 : Step primary s9 s10 := Step.sendReport s9 1 rfl rfl
  have election_valid : ElectionValid s10 e := by
    refine ⟨mask_majority, rfl, ?_, ?_⟩
    · intro i hi
      have cases_i : i = 0 ∨ i = 1 := by simpa [e, mask] using hi
      rcases cases_i with rfl | rfl <;> simp [e, s10, s9]
    · intro i _
      exact ⟨Nat.le_refl _, fun _ => Nat.le_refl _⟩
  have h11 : Step primary s10 s11 :=
    Step.elect s10 e rfl rfl rfl election_valid
  have reached : Reachable primary s11 :=
    Reachable.next (Reachable.next (Reachable.next (Reachable.next
    (Reachable.next (Reachable.next (Reachable.next (Reachable.next
    (Reachable.next (Reachable.next (Reachable.next Reachable.init h1)
      h2) h3) h4) h5) h6) h7) h8) h9) h10) h11
  refine ⟨primary, s11, reached, ?_, rfl, rfl, rfl, e, ?_, rfl, rfl⟩
  · simp [s11, s10, s9, s8, s7, s6, s5, updateNode, p]
  · simp [s11]

end Aegis
