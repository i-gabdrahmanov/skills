# Fixtures demo

- Controllers: **1**, endpoints: **2**
- Kafka topics: **4** (producers: 3, consumers: 2)

## Endpoints

| Method | Path | Handler | Params |
|---|---|---|---|
| POST | /api/v1/orders | OrderController.create | body:payload |
| GET | /api/v1/orders/{id} | OrderController.getById | path:id |

### OrderController

_File: `/Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/.gigacode/skills/java-uml-spec/test/fixtures/com/example/OrderController.java`_

| Field | Type |
|---|---|
| orderProducer | OrderProducer |

![OrderController sequence](fixtures-spec_diagrams/diagram_01_OrderController.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

actor Client
participant "OrderController" as Ctrl
participant "OrderProducer" as OrderProducer

== POST /api/v1/orders ==
Client -> Ctrl: create(payload)
Ctrl -> OrderProducer: publishOrder()
OrderProducer --> Ctrl
Ctrl --> Client: Void

== GET /api/v1/orders/{id} ==
Client -> Ctrl: getById(id)
Ctrl --> Client: String

@enduml
```

</details>

## Kafka

### Topics

| Topic | Producers | Consumers |
|---|---|---|
| orders.audit | OrderProducer | OrderConsumer |
| orders.dlq | — | OrderConsumer |
| orders.notifications | OrderConsumer | — |
| orders.v1 | OrderProducer | OrderConsumer |

### Producers

| Class | Method | Topics | File |
|---|---|---|---|
| OrderConsumer | onAudit | orders.notifications | /Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/.gigacode/skills/java-uml-spec/test/fixtures/com/example/OrderConsumer.java |
| OrderProducer | publishAudit | orders.audit | /Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/.gigacode/skills/java-uml-spec/test/fixtures/com/example/OrderProducer.java |
| OrderProducer | publishOrder | orders.v1 | /Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/.gigacode/skills/java-uml-spec/test/fixtures/com/example/OrderProducer.java |

### Consumers

| Class | Method | Topics | Group | File |
|---|---|---|---|---|
| OrderConsumer | onAudit | orders.audit, orders.dlq | order-audit | /Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/.gigacode/skills/java-uml-spec/test/fixtures/com/example/OrderConsumer.java |
| OrderConsumer | onOrder | orders.v1 | order-processors | /Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/.gigacode/skills/java-uml-spec/test/fixtures/com/example/OrderConsumer.java |

### Component diagram

![Kafka component](fixtures-spec_diagrams/diagram_02_kafka_component.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
!pragma layout smetana
skinparam componentStyle rectangle

component "OrderConsumer" as OrderConsumer
component "OrderProducer" as OrderProducer
queue "orders.audit" as topic_orders_audit
queue "orders.dlq" as topic_orders_dlq
queue "orders.notifications" as topic_orders_notifications
queue "orders.v1" as topic_orders_v1

OrderConsumer --> topic_orders_notifications : produce
OrderProducer --> topic_orders_audit : produce
OrderProducer --> topic_orders_v1 : produce
topic_orders_audit --> OrderConsumer : consume (order-audit)
topic_orders_dlq --> OrderConsumer : consume (order-audit)
topic_orders_v1 --> OrderConsumer : consume (order-processors)

@enduml
```

</details>

### Sequence diagram

![Kafka sequence](fixtures-spec_diagrams/diagram_03_kafka_sequence.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

participant "OrderConsumer" as OrderConsumer
participant "OrderProducer" as OrderProducer
queue "Kafka" as Kafka

== orders.audit ==
OrderProducer -> Kafka: send(orders.audit, publishAudit())
Kafka -> OrderConsumer: onAudit()

== orders.dlq ==
Kafka -> OrderConsumer: onAudit()

== orders.notifications ==
OrderConsumer -> Kafka: send(orders.notifications, onAudit())

== orders.v1 ==
OrderProducer -> Kafka: send(orders.v1, publishOrder())
Kafka -> OrderConsumer: onOrder()

@enduml
```

</details>
