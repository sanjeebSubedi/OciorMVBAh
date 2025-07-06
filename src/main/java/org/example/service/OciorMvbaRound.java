package org.example.service;

import org.example.model.Proposal;
import org.springframework.beans.factory.annotation.Autowired;

import java.io.ByteArrayOutputStream;
import java.util.*;

public class OciorMvbaRound {


    @Autowired
    private static ProposalService proposalService;
    final int N = 5, T = 1;
    final int quorum = (2 * T) + 1;
    Random rng = new Random();

    // Map of commitment -> collected shares
    Map<String, List<byte[]>> sharesMap = new HashMap<>();

    // Step 1: ACIDH phase — Disperse proposals
    void disperseProposals(List<Proposal> proposals) {
        for (Proposal p : proposals) {
            byte[][] shares = encodeMessageWithReedSolomon(p.getValue().getBytes());
            List<byte[]> collectedShares = new ArrayList<>();
            for (int i = 0; i < T + 1; i++) collectedShares.add(shares[i]); // Simulate collecting t+1 shares
            sharesMap.put(p.getCommitment(), collectedShares);
        }
    }

    // Step 2: Election phase
    Proposal electLeader(List<Proposal> proposals) {
        int idx = rng.nextInt(proposals.size());
        Proposal leader = proposals.get(idx);
        System.out.printf("🏅 Elected leader: %s → value: %s%n", leader.getCommitment(), leader.getValue());
        return leader;
    }

    // Step 3: ABBBA — quorum check based on votes about the leader
    int ABBBA(Proposal leader, List<Proposal> proposals) {
        long ones = proposals.stream()
                .filter(p -> p.getValue().equalsIgnoreCase(leader.getValue()))
                .count();
        long zeros = proposals.size() - ones;
        if (ones >= quorum) return 1;
        if (zeros >= quorum) return 0;
        return -1; // undecided → fallback to ABBA
    }

    // Step 4: ABBA — finalize binary agreement
    int ABBA(int a) {
        if (a == 0 || a == 1) return a;
        int coin = rng.nextBoolean() ? 1 : 0; // simulate common coin
        System.out.printf("⚖️ ABBA: no quorum → common coin yields: %d%n", coin);
        return coin;
    }

    // Step 5: DRh — reconstruct leader's proposal
    byte[] DRh(Proposal leader) throws Exception {
        List<byte[]> shares = sharesMap.get(leader.getCommitment());
        if (shares == null || shares.size() < T + 1) return null;
        return decodeMessageWithReedSolomon(shares);
    }

    // Predicate check — simple non-empty check for example
    boolean predicate(byte[] val) {
        return val != null && val.length > 0;
    }

    // Run the full round
   public String runMvbaRound(List<Proposal> proposals) throws Exception {
        disperseProposals(proposals);
        Proposal leader = electLeader(proposals);

        int a = ABBBA(leader, proposals);
        int b = ABBA(a);

        if (b == 1) {
            byte[] reconstructed = DRh(leader);
            if (predicate(reconstructed)) {
                String result = new String(reconstructed);
                System.out.printf("✅ Decided value: %s%n", result);
                return result;
            }
        }

        System.out.println("🔁 Repeat round: either b=0 or predicate failed.");
        return "⊥";
    }

    // Simplified encoding with zero-padded shares
    byte[][] encodeMessageWithReedSolomon(byte[] data) {
        int shardSize = (data.length + T - 1) / T;
        byte[][] shards = new byte[N][shardSize];
        for (int i = 0; i < T; i++) {
            int copyLen = Math.min(shardSize, data.length - i * shardSize);
            if (copyLen > 0) System.arraycopy(data, i * shardSize, shards[i], 0, copyLen);
        }
        return shards;
    }

    // Simplified decoding — merges first T shares
    byte[] decodeMessageWithReedSolomon(List<byte[]> subset) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (int i = 0; i < T && i < subset.size(); i++) out.write(subset.get(i));
        return out.toByteArray();
    }




}
