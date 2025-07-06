package org.example.controller;

import org.example.util.ChatClient;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.messaging.handler.annotation.MessageMapping;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Controller;

@Controller
public class ChatController {

    @Autowired
    private final SimpMessagingTemplate messagingTemplate;

    public ChatController(SimpMessagingTemplate messagingTemplate) {
        this.messagingTemplate = messagingTemplate;
    }

    @MessageMapping("/send")  // must match the "/app/send" prefix + destination on client
    public void receiveMessage(ChatClient.ChatMessage message) {
        // Broadcast the message to all clients subscribed to /topic/public
        messagingTemplate.convertAndSend("/topic/public", message);
    }
}
