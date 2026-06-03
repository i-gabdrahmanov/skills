package com.example;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.messaging.handler.annotation.SendTo;
import org.springframework.stereotype.Component;

@Component
public class OrderConsumer {

    public static final String TOPIC_ORDERS = "orders.v1";

    @KafkaListener(topics = TOPIC_ORDERS, groupId = "order-processors")
    public void onOrder(String payload) {
        System.out.println("got " + payload);
    }

    @KafkaListener(topics = {"orders.audit", "orders.dlq"}, groupId = "order-audit")
    @SendTo("orders.notifications")
    public String onAudit(String payload) {
        return "ack:" + payload;
    }
}
