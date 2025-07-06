package org.example.util;

import org.example.model.NodeDto;
import org.example.model.Share;
import org.example.service.AcidhDemo;
import org.springframework.messaging.converter.MappingJackson2MessageConverter;
import org.springframework.messaging.simp.stomp.*;
import org.springframework.util.concurrent.ListenableFuture;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.messaging.WebSocketStompClient;

import java.lang.reflect.Type;
import java.util.*;

public class ChatClient {


    public static String generate16BitUUID() {
        Random random = new Random();
        int value = random.nextInt(0x10000); // 0 to 0xFFFF
        return String.format("%04X", value); // e.g., "3A7F"
    }

    public static void main(String[] args) throws Exception {
        String url = "ws://localhost:8091/ws-chat/websocket";

        WebSocketStompClient stompClient = new WebSocketStompClient(new StandardWebSocketClient());
        stompClient.setMessageConverter(new MappingJackson2MessageConverter());

        StompSessionHandler sessionHandler = new MyStompSessionHandler();
        ListenableFuture<StompSession> future = stompClient.connect(url, sessionHandler);

        StompSession session = future.get(); // Wait for connection

        Scanner scanner = new Scanner(System.in);
        System.out.println("✅ Connected. Type your messages below:");
        List<NodeDto> nodeList=new ArrayList<>();
        while (true) {
            NodeDto nodeDto=new NodeDto();
            System.out.println("✅ Enter number of Nodes:");
            int node = scanner.nextInt();
            String name="";
            for (int i=1;i<=node;i++){
                String id=generate16BitUUID();
                nodeDto.setId(id);
                name="node"+i;
                nodeDto.setName(name);
                nodeList.add(nodeDto.clone());
            }
            List<Map<Integer, Share>> listOfMaps = new ArrayList<>();
            for (int i = 1; i <= 2; i++) {
                System.out.println("✅ Enter message for node:" + i);
                NodeDto nodeDto1 = nodeList.get(i-1);
                if(i==1) {
                    nodeDto1.setMessage("1");
                }
                else{
                    nodeDto1.setMessage("0");
                }
                nodeList.forEach(nodeDto2 -> {
                    if(nodeDto2.getId().equalsIgnoreCase(nodeDto1.getId())){
                        nodeDto2.setMessage(nodeDto1.getMessage());
                    }
                });
                Map<Integer, Share>  shareMap = AcidhDemo.ociorMbvah(session,nodeList,nodeDto1);
                listOfMaps.add(shareMap);
            }
            if(nodeList.size()==4) {
                nodeList.forEach(nodeDto1 -> {
                    session.send("/app/send", new ChatMessage(nodeDto1.getName(), listOfMaps.toString()));
                });
                session.disconnect();
            }
        }
    }

    private static class MyStompSessionHandler extends StompSessionHandlerAdapter {
        @Override
        public void afterConnected(StompSession session, StompHeaders connectedHeaders) {
            System.out.println("🎉 Connected to WebSocket server!");

            session.subscribe("/topic/public", new StompFrameHandler() {
                @Override
                public Type getPayloadType(StompHeaders headers) {
                    return ChatMessage.class;
                }

                @Override
                public void handleFrame(StompHeaders headers, Object payload) {
                    ChatMessage msg = (ChatMessage) payload;
                    System.out.printf("💬 %s: %s\n", msg.getSender(), msg.getContent());
                }
            });
            System.out.println("📡 Subscribed to /topic/public");
        }

        @Override
        public void handleTransportError(StompSession session, Throwable exception) {
            System.err.println("❌ Transport error: " + exception.getMessage());
        }
    }

    public static class ChatMessage {
        private String sender;
        private String content;

        public ChatMessage() {}  // required for Jackson

        public ChatMessage(String sender, String content) {
            this.sender = sender;
            this.content = content;
        }

        public String getSender() { return sender; }
        public void setSender(String sender) { this.sender = sender; }
        public String getContent() { return content; }
        public void setContent(String content) { this.content = content; }
    }
}
