package org.example.util;

import jakarta.websocket.Session;
import lombok.NoArgsConstructor;
import org.example.model.NodeDto;
import org.example.model.Share;
import org.springframework.messaging.simp.stomp.StompSession;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@NoArgsConstructor
public class AcidhProtocol {

    static int N=0;
    static int T = 1;

    public AcidhProtocol(int N){
        this.N=N;

    }

    static int quorum = N - T;          // n-t threshold for votes/locks/etc.
    final int confirmQuorum = (2 * T) + 1; // 2t+1 for final confirms

    // Protocol state maps
    Map<Integer, Integer> L_lock = new HashMap<>();
    Map<Integer, Integer> R_ready = new HashMap<>();
    Map<Integer, Integer> F_finish = new HashMap<>();
    Map<Integer, Share> S_shares = new HashMap<>();
    Map<String, Integer> H_hash = new HashMap<>();

    Map<String, Integer> votes = new HashMap<>();
    Map<String, String> voteMessage = new HashMap<>();
    Map<String, Integer> locks = new HashMap<>();

    Map<String, String> lockMessage = new HashMap<>();
    Map<String, Integer> readies = new HashMap<>();

    Map<String, String> readyMessage = new HashMap<>();
    Map<String, Integer> finishes = new HashMap<>();

    Map<String, String> finishMessage = new HashMap<>();
    Map<String, Integer> elections = new HashMap<>();

    Map<String, String> electionMessage = new HashMap<>();
    Map<String, Integer> confirms = new HashMap<>();

    Map<String, String> confirmMessage = new HashMap<>();



    // ACID-share phase: proposer disperses its proposal
    public  Map<Integer, Share> shareProposal(StompSession session,
                                              List<NodeDto> nodeDtos,
                                              String id,
                                              byte[][] shares,
                                              MerkleTree tree) throws Exception {
        String commitment = bytesToHex(tree.buildRoot());
        for (int j = 1; j <= N; j++) {
            byte[] yj = shares[j - 1];
            List<byte[]> proof = tree.getProof(j - 1);
            return sendShare(session,nodeDtos,id, commitment, yj, proof, j);
        }
        return null;
    }

    // Simulate sending ("SHARE", ID, C, yj, ωj) to Pj
    Map<Integer, Share> sendShare(StompSession session,
                                  List<NodeDto> nodeDtos,
                                  String id, String commitment, byte[] share,
                                  List<byte[]> proof, int nodeId) throws Exception {
        // VcVerify step
        boolean verified = MerkleTree.verifyProof(nodeId - 1, share, proof, hexToBytes(commitment));
        if (verified) {
            S_shares.put(nodeId, new Share(commitment, share, proof));
            H_hash.put(commitment, nodeId);
            sendVote(id,session,commitment, nodeDtos);
            return S_shares;
        }
        return null;
    }

    // ACID-vote phase: send ("VOTE", ID, C) to all
    void sendVote(String id, StompSession session , String commitment,
                  List<NodeDto> nodeDtos) {
        votes.put(commitment, votes.getOrDefault(commitment, 0) + 1);
        Optional<NodeDto> optionalNodeDto=
                nodeDtos.stream().filter(nodeDto1 -> nodeDto1.getId().equalsIgnoreCase(id))
                        .findFirst();
        optionalNodeDto.ifPresent(nodeDto -> voteMessage.put(nodeDto.getName()+ " VOTE ", votes.toString()));
        if (votes.get(commitment) >= quorum) {
            sendLock(id, commitment,session,nodeDtos);
            nodeDtos.forEach(nodeDto -> {
                WebSocketMessage.sendMessage(session, nodeDto, voteMessage);
            });
        }
    }

    // ACID-lock phase: send ("LOCK", ID, C) to all
    void sendLock(String id, String commitment,StompSession session ,List<NodeDto> nodeDtos) {
        locks.put(commitment, locks.getOrDefault(commitment, 0) + 1);
        Optional<NodeDto> optionalNodeDto=
                nodeDtos.stream().filter(nodeDto1 -> nodeDto1.getId().equalsIgnoreCase(id))
                        .findFirst();
        optionalNodeDto.ifPresent(nodeDto -> lockMessage.put(nodeDto.getName()+ " LOCK ", locks.toString()));
        if (locks.get(commitment) >= quorum) {
            int jStar = H_hash.get(commitment);
            L_lock.put(jStar, 1);
            nodeDtos.forEach(nodeDto -> {
                WebSocketMessage.sendMessage(session, nodeDto, lockMessage);
            });
            sendReady(id, commitment, session, nodeDtos);
        }
    }

    // ACID-ready phase: send ("READY", ID, C) to all
    void sendReady(String id, String commitment,StompSession session ,List<NodeDto> nodeDtos) {
        readies.put(commitment, readies.getOrDefault(commitment, 0) + 1);
        Optional<NodeDto> optionalNodeDto=
                nodeDtos.stream().filter(nodeDto1 -> nodeDto1.getId().equalsIgnoreCase(id))
                        .findFirst();
        optionalNodeDto.ifPresent(nodeDto -> readyMessage.put(nodeDto.getName()+ " READY ", locks.toString()));
        if (readies.get(commitment) >= quorum) {
            int jStar = H_hash.get(commitment);
            R_ready.put(jStar, 1);
            nodeDtos.forEach(nodeDto -> {
                WebSocketMessage.sendMessage(session, nodeDto, readyMessage);
            });
            sendFinish(id, commitment, jStar, session, nodeDtos);
        }
    }

    // ACID-finish phase: send ("FINISH", ID) to proposer
    void sendFinish(String id, String commitment, int jStar,StompSession session ,List<NodeDto> nodeDtos) {
        finishes.put(commitment, finishes.getOrDefault(commitment, 0) + 1);
        Optional<NodeDto> optionalNodeDto=
                nodeDtos.stream().filter(nodeDto1 -> nodeDto1.getId().equalsIgnoreCase(id))
                        .findFirst();
        optionalNodeDto.ifPresent(nodeDto -> finishMessage.put(nodeDto.getName()+ " FINISH ", finishes.toString()));
        if (finishes.get(commitment) >= quorum) {
            F_finish.put(jStar, 1);
            nodeDtos.forEach(nodeDto -> {
                WebSocketMessage.sendMessage(session, nodeDto, finishMessage);
            });
            sendElection(id, commitment, session, nodeDtos);
        }
    }

    // Vote for election phase
    void sendElection(String id, String commitment,StompSession session ,List<NodeDto> nodeDtos) {
        elections.put(commitment, elections.getOrDefault(commitment, 0) + 1);
        Optional<NodeDto> optionalNodeDto=
                nodeDtos.stream().filter(nodeDto1 -> nodeDto1.getId().equalsIgnoreCase(id))
                        .findFirst();
        optionalNodeDto.ifPresent(nodeDto -> electionMessage.put(nodeDto.getName()+ " ELECTION ", elections.toString()));
        if (elections.get(commitment) >= quorum) {
            nodeDtos.forEach(nodeDto -> {
                WebSocketMessage.sendMessage(session, nodeDto, electionMessage);
            });
            sendConfirm(id, commitment, session, nodeDtos);
        }
    }

    // Confirm phase
    void sendConfirm(String id, String commitment,StompSession session ,List<NodeDto> nodeDtos) {
        confirms.put(commitment, confirms.getOrDefault(commitment, 0) + 1);
        Optional<NodeDto> optionalNodeDto=
                nodeDtos.stream().filter(nodeDto1 -> nodeDto1.getId().equalsIgnoreCase(id))
                        .findFirst();
        optionalNodeDto.ifPresent(nodeDto -> confirmMessage.put(nodeDto.getName()+ " CONFIRM ", confirms.toString()));

        if (confirms.get(commitment) >= confirmQuorum) {
            nodeDtos.forEach(nodeDto -> {
                WebSocketMessage.sendMessage(session, nodeDto, confirmMessage);
            });
//            System.out.printf("🎉 Received %d CONFIRM → ACID complete for %s%n",
//                    confirms.get(commitment), commitment);
            returnState();
        }
    }

    // ACID returns all relevant states when complete
    void returnState() {
//        System.out.println("📤 ACID returning:");
//        System.out.printf("  Locks: %s%n", L_lock);
//        System.out.printf("  Ready: %s%n", R_ready);
//        System.out.printf("  Finish: %s%n", F_finish);
//        System.out.printf("  Shares: %s%n", S_shares.keySet());
    }

    static String bytesToHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder();
        for (byte b : bytes) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    static byte[] hexToBytes(String hex) {
        int len = hex.length();
        byte[] data = new byte[len/2];
        for (int i=0; i<len; i+=2) data[i/2]=(byte)((Character.digit(hex.charAt(i),16)<<4)
                +Character.digit(hex.charAt(i+1),16));
        return data;
    }
}
