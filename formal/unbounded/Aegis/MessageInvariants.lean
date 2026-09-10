import Aegis.Protocol

namespace Aegis

private theorem electionValid_reports_mono {n : Nat} {Command : Type}
    {s t : State n Command} {e : Election n Command}
    (included : ∀ r, r ∈ s.reports → r ∈ t.reports)
    (valid : ElectionValid s e) : ElectionValid t e := by
  obtain ⟨majority, selected, reported, maximal⟩ := valid
  exact ⟨majority, selected, fun r hr => included _ (reported r hr), maximal⟩

private theorem certified_votes_mono {n : Nat} {Command : Type}
    {s t : State n Command} {v : Nat} {p : Log Command}
    (included : ∀ vote, vote ∈ s.votes → vote ∈ t.votes)
    (cert : Certified s v p) : Certified t v p := by
  obtain ⟨voters, majority, voted⟩ := cert
  refine ⟨voters, majority, ?_⟩
  intro r member
  obtain ⟨l, vote_mem, is_prefix⟩ := voted r member
  exact ⟨l, included _ vote_mem, is_prefix⟩

theorem sendReport_preserves {n : Nat} {Command : Type}
    {primary : Nat → Fin n} {s : State n Command} (r : Fin n)
    (inv : LocalInvariant primary s) (changing : (s.nodes r).changing = true) :
    LocalInvariant primary
      { s with reports := ⟨r, (s.nodes r).promise, (s.nodes r).accepted⟩ :: s.reports } := by
  refine {
    history := ?_
    accepted_proposed := inv.accepted_proposed
    accepted_le_promise := inv.accepted_le_promise
    normal_view := inv.normal_view
    changing_view := inv.changing_view
    primary_latest := inv.primary_latest
    votes_current := inv.votes_current
    reports_promised := ?_
    commits_certified := inv.commits_certified
    applied_certified := inv.applied_certified
    applied_view := inv.applied_view
  }
  · refine {
      proposals_origin := inv.history.proposals_origin
      proposals_comparable := inv.history.proposals_comparable
      votes_proposed := inv.history.votes_proposed
      reports_proposed := ?_
      reports_lower := ?_
      reports_fence := ?_
      elections_valid := ?_
      elections_proposed := inv.history.elections_proposed
    }
    · intro report member
      rcases List.mem_cons.mp member with equal | old
      · subst report
        exact inv.accepted_proposed r
      · exact inv.history.reports_proposed report old
    · intro report member
      rcases List.mem_cons.mp member with equal | old
      · subst report
        exact inv.changing_view r changing
      · exact inv.history.reports_lower report old
    · intro report member vote vote_mem sender lower
      rcases List.mem_cons.mp member with equal | old
      · subst report
        have current := inv.votes_current vote vote_mem
        change vote.sender = r at sender
        simpa only [sender] using current
      · exact inv.history.reports_fence report old vote vote_mem sender lower
    · intro election member
      apply electionValid_reports_mono _ (inv.history.elections_valid election member)
      intro report old
      exact List.mem_cons_of_mem _ old
  · intro report member
    rcases List.mem_cons.mp member with equal | old
    · subst report
      exact Nat.le_refl _
    · exact inv.reports_promised report old

theorem acknowledge_preserves {n : Nat} {Command : Type}
    {primary : Nat → Fin n} {s : State n Command} (r : Fin n)
    (inv : LocalInvariant primary s) (normal : (s.nodes r).changing = false) :
    LocalInvariant primary
      { s with votes := ⟨r, (s.nodes r).accepted.view, (s.nodes r).accepted.log⟩ :: s.votes } := by
  refine {
    history := ?_
    accepted_proposed := inv.accepted_proposed
    accepted_le_promise := inv.accepted_le_promise
    normal_view := inv.normal_view
    changing_view := inv.changing_view
    primary_latest := inv.primary_latest
    votes_current := ?_
    reports_promised := inv.reports_promised
    commits_certified := ?_
    applied_certified := inv.applied_certified
    applied_view := inv.applied_view
  }
  · refine {
      proposals_origin := inv.history.proposals_origin
      proposals_comparable := inv.history.proposals_comparable
      votes_proposed := ?_
      reports_proposed := inv.history.reports_proposed
      reports_lower := inv.history.reports_lower
      reports_fence := ?_
      elections_valid := inv.history.elections_valid
      elections_proposed := inv.history.elections_proposed
    }
    · intro vote member
      rcases List.mem_cons.mp member with equal | old
      · subst vote
        exact inv.accepted_proposed r
      · exact inv.history.votes_proposed vote old
    · intro report report_mem vote vote_mem sender lower
      rcases List.mem_cons.mp vote_mem with equal | old
      · subst vote
        have promised := inv.reports_promised report report_mem
        have accepted := inv.normal_view r normal
        change r = report.sender at sender
        change (s.nodes r).accepted.view < report.view at lower
        rw [← sender] at promised
        exfalso
        omega
      · exact inv.history.reports_fence report report_mem vote old sender lower
  · intro vote member
    rcases List.mem_cons.mp member with equal | old
    · subst vote
      exact ⟨Nat.le_refl _, fun _ => List.prefix_refl _⟩
    · exact inv.votes_current vote old
  · intro proposal member
    apply certified_votes_mono _ (inv.commits_certified proposal member)
    intro vote old
    exact List.mem_cons_of_mem _ old

theorem commit_preserves {n : Nat} {Command : Type}
    {primary : Nat → Fin n} {s : State n Command} (p : Proposal Command)
    (inv : LocalInvariant primary s) (cert : Certified s p.view p.log) :
    LocalInvariant primary { s with commits := p :: s.commits } := by
  refine {
    history := {
      proposals_origin := inv.history.proposals_origin
      proposals_comparable := inv.history.proposals_comparable
      votes_proposed := inv.history.votes_proposed
      reports_proposed := inv.history.reports_proposed
      reports_lower := inv.history.reports_lower
      reports_fence := inv.history.reports_fence
      elections_valid := inv.history.elections_valid
      elections_proposed := inv.history.elections_proposed
    }
    accepted_proposed := inv.accepted_proposed
    accepted_le_promise := inv.accepted_le_promise
    normal_view := inv.normal_view
    changing_view := inv.changing_view
    primary_latest := inv.primary_latest
    votes_current := inv.votes_current
    reports_promised := inv.reports_promised
    commits_certified := ?_
    applied_certified := ?_
    applied_view := inv.applied_view
  }
  · intro proposal member
    rcases List.mem_cons.mp member with equal | old
    · subst proposal
      exact cert
    · exact inv.commits_certified proposal old
  · intro r
    rcases inv.applied_certified r with empty | old
    · exact Or.inl empty
    · exact Or.inr (List.mem_cons_of_mem _ old)

end Aegis
