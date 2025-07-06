package org.example.util;

import org.example.model.Proposal;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

public class MvbaRoundWithElection {

    final int N = 5;
    final int T = 1;
    final int quorum = (2 * T) + 1;

    Random rng = new Random();

    public String mvbaRound(List<Proposal> proposals) throws Exception {
        // 1) Validate proposals using DRh + predicate
        List<Proposal> validatedProposals = new ArrayList<>();
        for (Proposal p : proposals) {
            if (validateProposal(p)) {
                validatedProposals.add(p);
            }
        }
        if (validatedProposals.isEmpty()) {
            System.out.println("❌ No valid proposals to consider.");
            return "⊥";
        }

        // 2) Election: pick random leader among validated proposals
        Proposal leaderProposal = electRandomLeader(validatedProposals);
        System.out.printf("🏅 Elected leader proposal: %s → value: %s%n",
                leaderProposal.getCommitment(), leaderProposal.getValue());

        // 3) Run ABBA on leader proposal
        List<Integer> votes = simulateVotes(leaderProposal, proposals);
        int decision = runAbba(votes);

        if (decision == 1) {
            System.out.printf("✅ ABBA accepted proposal: %s → value: %s%n",
                    leaderProposal.getCommitment(), leaderProposal.getValue());
            return leaderProposal.getValue();
        } else {
            System.out.println("⊥ ABBA rejected the leader's proposal.");
            return "⊥";
        }
    }

    boolean validateProposal(Proposal proposal) {
        // Here you'd reconstruct with DRh; we'll just simulate validation
        return proposal.getValue() != null && !proposal.getValue().isEmpty();
    }

    Proposal electRandomLeader(List<Proposal> validatedProposals) {
        // Election primitive → random leader selection among valid proposals
        int index = rng.nextInt(validatedProposals.size());
        return validatedProposals.get(index);
    }

    List<Integer> simulateVotes(Proposal leader, List<Proposal> proposals) {
        List<Integer> votes = new ArrayList<>();
        // Simulate votes based on proposals supporting leader's value
        for (Proposal p : proposals) {
            if (p.getValue().equals(leader.getValue())) {
                votes.add(1);  // vote yes
            } else {
                votes.add(0);  // vote no
            }
        }
        return votes;
    }

    int runAbba(List<Integer> votes) {
        int zeros = 0, ones = 0;
        for (int v : votes) {
            if (v == 0) zeros++;
            else if (v == 1) ones++;
        }

        if (ones >= quorum) return 1;   // accepted
        if (zeros >= quorum) return 0;  // rejected

        // No quorum → fallback to common coin for convergence
        int coin = rng.nextBoolean() ? 1 : 0;
        System.out.printf("⚖️ ABBA: no quorum → common coin yields: %d%n", coin);
        return coin;
    }
}
