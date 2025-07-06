package org.example.util;

import lombok.experimental.UtilityClass;
import org.example.model.NodeDto;
import org.springframework.messaging.simp.stomp.StompSession;
import java.util.Map;

@UtilityClass
public class WebSocketMessage {


    public static void sendMessage(StompSession session ,NodeDto nodeDto,Map<String, String> shareMap){
        session.send("/app/send", new ChatClient.ChatMessage(nodeDto.getName(), shareMap.toString()));
    }


}
