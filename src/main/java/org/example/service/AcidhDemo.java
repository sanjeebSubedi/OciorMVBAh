package org.example.service;

import com.mysql.cj.Session;
import org.example.model.NodeDto;
import org.example.model.Share;
import org.example.util.AcidhProtocol;
import org.example.util.MerkleTree;
import org.example.util.OciorMvbahAlgorithm8;
import org.springframework.messaging.simp.stomp.StompSession;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

public class AcidhDemo {


    public static Map<Integer, Share> ociorMbvah(StompSession session,
                                                 List<NodeDto> nodeDtos,
                                                 NodeDto nodeDto) throws Exception {
        byte[][] shares = OciorMvbahAlgorithm8.encodeMessageWithReedSolomon(nodeDto.getMessage().getBytes());
        List<byte[]> shareList = new ArrayList<>(Arrays.asList(shares));
        MerkleTree tree = new MerkleTree(shareList);
        AcidhProtocol acidhProtocol=new AcidhProtocol(nodeDtos.size());
        return acidhProtocol.shareProposal(session,nodeDtos,nodeDto.getId(), shares, tree);
    }

    public static void main(String[] args) throws Exception {
        AcidhProtocol acidhProtocol = new AcidhProtocol();

        String value="0";
        // 1) Generate proposal data
        byte[] proposalData = value.getBytes();

        byte[][] shares = OciorMvbahAlgorithm8.encodeMessageWithReedSolomon(proposalData);

        // 3) Build Merkle tree over shares
        List<byte[]> shareList = new ArrayList<>(Arrays.asList(shares));

        MerkleTree tree = new MerkleTree(shareList);

        String commitmentHex = bytesToHex(tree.buildRoot());


        int nodeId = 1;
        int index = nodeId - 1;
        byte[] share = shares[index];
        List<byte[]> proof = tree.getProof(index);

        // 7) Verify the proof
        boolean verified = MerkleTree.verifyProof(
                index,
                share,
                proof,
                hexToBytes(commitmentHex)
        );

        // 8) Output verification result
        System.out.println("Verified: " + verified);
    }

    // Helper to convert byte[] to hex string
    public static String bytesToHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder();
        for (byte b : bytes) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    // Helper to convert hex string back to byte[]
    public static byte[] hexToBytes(String hex) {
        int len = hex.length();
        byte[] result = new byte[len / 2];
        for (int i = 0; i < len; i += 2)
            result[i / 2] = (byte) ((Character.digit(hex.charAt(i), 16) << 4)
                    + Character.digit(hex.charAt(i+1), 16));
        return result;
    }
}
