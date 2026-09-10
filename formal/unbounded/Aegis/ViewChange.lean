import Aegis.Protocol

namespace Aegis

private theorem comparable_prefix_of_length {α : Type} {a b : List α}
    (comparable : a <+: b ∨ b <+: a) (lengths : a.length ≤ b.length) : a <+: b := by
  rcases comparable with direct | reverse
  · exact direct
  · have equal : b = a := reverse.eq_of_length_le lengths
    simpa only [equal] using List.prefix_refl a

theorem leaderCompleteness {n : Nat} {Command : Type} {s : State n Command}
    {c b : Nat} {p l : Log Command}
    (facts : HistoryFacts s) (cert : Certified s c p)
    (prop : Proposal.mk b l ∈ s.proposals) (lt : c < b) : p <+: l := by
  induction b using Nat.strongRecOn generalizing c p l with
  | ind b ih =>
    rcases facts.proposals_origin (Proposal.mk b l) prop with zero | ⟨e, he, view_eq, base_prefix⟩
    · change b = 0 at zero
      exfalso
      omega
    · rcases facts.elections_valid e he with ⟨e_majority, selected_voter, reported, maximal⟩
      obtain ⟨voters, cert_majority, voted⟩ := cert
      obtain ⟨r, cert_voter, election_voter⟩ := majority_intersection cert_majority e_majority
      obtain ⟨vote_log, vote_mem, p_vote⟩ := voted r cert_voter
      have report_mem := reported r election_voter
      have selected_report_mem := reported e.selected selected_voter
      have before_report : c < e.view := by
        change e.view = b at view_eq
        omega
      have fence := facts.reports_fence
        (Report.mk r e.view (e.reports r)) report_mem
        (Vote.mk r c vote_log) vote_mem rfl before_report
      have report_lower := facts.reports_lower _ report_mem
      have selected_lower := facts.reports_lower _ selected_report_mem
      have report_proposed := facts.reports_proposed _ report_mem
      have selected_proposed := facts.reports_proposed _ selected_report_mem
      have original_cert : Certified s c p := ⟨voters, cert_majority, voted⟩
      have p_report : p <+: (e.reports r).log := by
        by_cases same_view : c = (e.reports r).view
        · exact p_vote.trans (fence.2 same_view)
        · have smaller : (e.reports r).view < b := by
            change (e.reports r).view < e.view at report_lower
            change e.view = b at view_eq
            omega
          have higher : c < (e.reports r).view := by
            have ordered : c ≤ (e.reports r).view := fence.1
            omega
          exact ih (e.reports r).view smaller original_cert report_proposed higher
      have p_base : p <+: e.base := by
        obtain ⟨rank_view, rank_length⟩ := maximal r election_voter
        by_cases same_view : (e.reports r).view = (e.reports e.selected).view
        · have comparable := facts.proposals_comparable
            (Proposal.mk (e.reports r).view (e.reports r).log) report_proposed
            (Proposal.mk (e.reports e.selected).view (e.reports e.selected).log)
            selected_proposed same_view
          exact p_report.trans (comparable_prefix_of_length comparable (rank_length same_view))
        · have smaller : (e.reports e.selected).view < b := by
            change (e.reports e.selected).view < e.view at selected_lower
            change e.view = b at view_eq
            omega
          have higher : c < (e.reports e.selected).view := by
            have ordered : c ≤ (e.reports r).view := fence.1
            omega
          exact ih (e.reports e.selected).view smaller original_cert selected_proposed higher
      exact p_base.trans base_prefix

private theorem certified_proposal {n : Nat} {Command : Type} {s : State n Command}
    {v : Nat} {p : Log Command} (facts : HistoryFacts s) (cert : Certified s v p) :
    ∃ l, Proposal.mk v l ∈ s.proposals ∧ p <+: l := by
  obtain ⟨voters, majority, voted⟩ := cert
  obtain ⟨r, member, _⟩ := majority_intersection majority majority
  obtain ⟨l, vote_mem, is_prefix⟩ := voted r member
  exact ⟨l, facts.votes_proposed (Vote.mk r v l) vote_mem, is_prefix⟩

theorem certificates_compatible {n : Nat} {Command : Type} {s : State n Command}
    {c d : Nat} {p q : Log Command}
    (facts : HistoryFacts s) (cert1 : Certified s c p) (cert2 : Certified s d q) :
    p <+: q ∨ q <+: p := by
  obtain ⟨lp, proposed_p, prefix_p⟩ := certified_proposal facts cert1
  obtain ⟨lq, proposed_q, prefix_q⟩ := certified_proposal facts cert2
  by_cases smaller : c < d
  · exact List.prefix_or_prefix_of_prefix
      (leaderCompleteness facts cert1 proposed_q smaller) prefix_q
  · by_cases equal : c = d
    · have comparable := facts.proposals_comparable
        (Proposal.mk c lp) proposed_p (Proposal.mk d lq) proposed_q equal
      rcases comparable with forward | reverse
      · exact List.prefix_or_prefix_of_prefix (prefix_p.trans forward) prefix_q
      · exact List.prefix_or_prefix_of_prefix prefix_p (prefix_q.trans reverse)
    · have greater : d < c := by omega
      exact List.prefix_or_prefix_of_prefix prefix_p
        (leaderCompleteness facts cert2 proposed_p greater)

end Aegis
