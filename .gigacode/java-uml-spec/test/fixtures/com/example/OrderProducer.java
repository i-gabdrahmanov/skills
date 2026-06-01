package com.example;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

@Service
public class OrderProducer {

    public static final String TOPIC_ORDERS = "orders.v1";

    private final KafkaTemplate<String, String> kafkaTemplate;

    public OrderProducer(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publishOrder(String payload) {
        kafkaTemplate.send(TOPIC_ORDERS, payload);
    }

    public void publishAudit(String payload) {
        kafkaTemplate.send("orders.audit", payload);
    }
}
