package org.example.util;

import java.security.MessageDigest;
import java.util.List;

public class ShareUtils {
    public static byte[][] encodeMessage(byte[] msg, int n) {
        byte[][] shares = new byte[n*3][1];
        for (int i = 0; i < msg.length; i++) shares[i][0] = msg[i];
        for (int i = msg.length; i < n; i++) shares[i][0] = 0;
        return shares;
    }

    public static byte[] decodeMessage(List<byte[]> shares, int len) {
        byte[] result = new byte[len];
        int collected = 0;

        for (int i = 0; i < shares.size() && collected < len; i++) {
            byte[] share = shares.get(i);
            // take exactly first byte of each share
            result[collected++] = share[0];
        }

        return result;
    }


    public static String commit(byte[] shares) throws Exception {
        MessageDigest d = MessageDigest.getInstance("SHA-256");

        for (byte s : shares) {
            d.update(s);
        }
        return bytesToHex(d.digest());
    }

    public static String computeProof(byte[] y, int id) throws Exception {
        MessageDigest d = MessageDigest.getInstance("SHA-256");
        d.update(y);
        d.update(Integer.toString(id).getBytes());
        return bytesToHex(d.digest());
    }

    public static boolean verifyProof(int id, byte[] y, String omega) throws Exception {
        return computeProof(y, id).equals(omega);
    }

    public static String bytesToHex(byte[] b) {
        StringBuilder sb = new StringBuilder();
        for (byte x : b) sb.append(String.format("%02x", x));
        return sb.toString();
    }
}
