package org.example.util;

import com.backblaze.erasure.ReedSolomon;
import org.example.model.Proposal;
import org.example.respository.ProposalRepository;
import org.springframework.beans.factory.annotation.Autowired;

import java.io.ByteArrayOutputStream;
import java.util.*;

public class OciorMvbahAlgorithm8 {

    @Autowired
    private ProposalRepository proposalRepository;

    static int N = 2, T = 1; // total nodes, tolerated faults
    static final int quorum = (2 * T) + 1;

    int messageLength;

    Map<Integer, byte[][]> S_shares = new HashMap<>();
    Map<Integer, Integer> L_lock = new HashMap<>();
    Map<Integer, Integer> R_ready = new HashMap<>();
    Map<Integer, Integer> F_finish = new HashMap<>();
    Map<Integer, String> H_hash = new HashMap<>();

    public String mvbahRound(List<Proposal> proposals) throws Exception {
        N=proposals.size();
        initNodeStates(N);

        // Disperse proposals using Reed-Solomon (ACIDH)
        for (Proposal p : proposals) {
            byte[] value = p.getValue().getBytes();
            byte[][] shares = encodeMessageWithReedSolomon(value);

            int nodeId = p.getNode().getId().intValue();
            for (int i = 0; i < T + 1; i++) {
                S_shares.get(nodeId)[i] = shares[i];
            }
            H_hash.put(nodeId, p.getCommitment());
        }

        Map<String, Integer> valueCounts = new HashMap<>();

        for (int nodeId = 1; nodeId <= N; nodeId++) {
            List<Proposal> proposalList = proposalRepository.findByNodeId(Long.parseLong(String.valueOf(nodeId)));
            if (proposalList.isEmpty()) continue;

            // Collect binary votes for proposals of this node
            List<Integer> receivedVotes = new ArrayList<>();
            for (Proposal proposal : proposalList) {
                int localVote = ABBBA(proposal, proposalList);
                receivedVotes.add(localVote);
            }

            // Run ABBA on collected votes
            int b = ABBA(receivedVotes);

            // Count votes supporting the proposal's value to check for quorum
            long validVotes = proposalList.stream()
                    .filter(p -> p.getValue().equalsIgnoreCase(proposalList.get(0).getValue()))
                    .count();

            if (validVotes >= quorum && (b == 1 || b == 0)) {
                R_ready.put(nodeId, 1);
                F_finish.put(nodeId, 1);
            } else {
                R_ready.put(nodeId, 0);
                F_finish.put(nodeId, 0);
            }

            if (b == 1 || b == 0) {
                messageLength = proposalList.get(0).getValue().length();
                byte[] val = DRh(String.valueOf(nodeId));
                if (predicate(val)) {
                    String sVal = new String(val);
                    valueCounts.put(sVal, valueCounts.getOrDefault(sVal, 0) + 1);
                }
            }
        }

        int majorityThreshold = (proposals.size() / 2) + 1;
        for (Map.Entry<String, Integer> entry : valueCounts.entrySet()) {
            if (entry.getValue() >= majorityThreshold) {
                return entry.getKey();
            }
        }

        return "⊥";
    }

    void initNodeStates(int n) {
        for (int j = 1; j <= n; j++) {
            S_shares.put(j, new byte[][] { null, null, null });
            L_lock.put(j, 0);
            R_ready.put(j, 0);
            F_finish.put(j, 0);
            H_hash.put(j, null);
        }
    }

    byte[] DRh(String C) throws Exception {
        int nodeId = Integer.parseInt(C);
        byte[][] shares = S_shares.get(nodeId);
        List<byte[]> subset = new ArrayList<>();
        for (byte[] share : shares) {
            if (share != null) subset.add(share);
        }
        if (subset.size() < T + 1) return null;
        return decodeMessageWithReedSolomon(subset);
    }

    boolean predicate(byte[] msg) {
        return msg != null && msg.length > 0;
    }

    int ABBBA(Proposal proposal, List<Proposal> proposals) {
        long zeros = proposals.stream()
                .filter(p -> p.getValue().equalsIgnoreCase("0")).count();
        long ones = proposals.stream()
                .filter(p -> p.getValue().equalsIgnoreCase("1")).count();
        if (proposal.getValue().equalsIgnoreCase("1") && ones >= quorum) return 1;
        if (proposal.getValue().equalsIgnoreCase("0") && zeros >= quorum) return 0;
        return -1;
    }

    int ABBA(List<Integer> receivedVotes) {
        int zeros = 0, ones = 0;
        for (int v : receivedVotes) {
            if (v == 0) zeros++;
            else if (v == 1) ones++;
        }
        if (ones >= quorum) return 1;
        if (zeros >= quorum) return 0;

        int coin = new Random().nextBoolean() ? 1 : 0;
        System.out.printf("⚖️ ABBA: no quorum → common coin yields: %d%n", coin);
        return coin;
    }

    public  static byte[][] encodeMessageWithReedSolomon(byte[] data) {
        int shardSize = (data.length + T - 1) / T;
        int parityShards = N - T;
        byte[][] shards = new byte[N][shardSize];

        for (int i = 0; i < T; i++) {
            int copyLen = Math.min(shardSize, data.length - i * shardSize);
            if (copyLen > 0) {
                System.arraycopy(data, i * shardSize, shards[i], 0, copyLen);
            }
        }

        ReedSolomon reedSolomon = ReedSolomon.create(T, parityShards);
        reedSolomon.encodeParity(shards, 0, shardSize);

        return shards;
    }

    byte[] decodeMessageWithReedSolomon(List<byte[]> subset) throws Exception {
        int shardSize = subset.get(0).length;
        byte[][] shards = new byte[N][shardSize];
        boolean[] shardPresent = new boolean[N];

        for (int i = 0; i < subset.size(); i++) {
            shards[i] = subset.get(i);
            shardPresent[i] = true;
        }

        ReedSolomon reedSolomon = ReedSolomon.create(T, N - T);
        reedSolomon.decodeMissing(shards, shardPresent, 0, shardSize);

        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (int i = 0; i < T; i++) out.write(shards[i]);
        return out.toByteArray();
    }
}
