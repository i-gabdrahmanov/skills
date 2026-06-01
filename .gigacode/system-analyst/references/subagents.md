# Промпты субагентов для system-analyst

Каждый субагент работает с чистого старта. Субагенты этапа 2 вызываются
параллельно (одной или двумя волнами — см. SKILL.md "Масштабирование"). Каждому
передаются: путь к корню, список модулей (из structure-mapper), и скоуп.

## Общие входные данные (включай в каждый промпт)

```
Корень проекта: <git toplevel>
Модули проекта (из structure-mapper):
  - proxy-service: src в proxy-service/src/main/java
  - springproxy:   src в springproxy/src/main/java
```

## Правило компактного вывода (важно для больших проектов)

Если у проекта **более 50 ожидаемых элементов** в категории субагента
(endpoints, entities, и т.п.), **обязательно** добавь в промпт фразу:

> "Будь лаконичен. Если элементов больше 30, верни:
>  (1) счётчики по группировке (`counts_by_module`, `counts_by_domain`);
>  (2) топ-10 представительных примеров на группу;
>  (3) полный список — кратким видом (только обязательные поля: module + identifier).
>  НЕ выводи каждый элемент полностью — это превысит лимит."

Это особенно касается **api-mapper** (часто >50 endpoints) и
**domain-mapper** (часто >30 entities). Без этой подсказки оба возвращают
>50KB вывод, который сохраняется в persisted file и требует post-processing.

Если ты оркестратор и видишь, что субагент всё-таки вернул persisted file
("Output too large"), не паникуй — можно извлечь JSON через Python из файла
(см. `scripts/extract_persisted.py`, если он будет создан).

---

## 3.1. api-mapper

```
description: "Map REST API endpoints"
subagent_type: general-purpose

prompt:
Собери каталог REST-эндпоинтов из Java/Spring-проекта.

Корень проекта: <root>
Модули: <module list>

Шаги:
1. Во ВСЕХ модулях найди классы с @RestController или @Controller:
   grep -rE '@(Rest)?Controller' <module>/src/main/java
2. Для каждого класса найди:
   - Базовый путь из @RequestMapping на классе (если есть).
   - Все методы с @GetMapping, @PostMapping, @PutMapping, @DeleteMapping,
     @PatchMapping, @RequestMapping(method=...).
3. Для каждого endpoint извлеки:
   - HTTP метод
   - Полный путь (base + method path)
   - Имя метода-обработчика (для ссылок)
   - Тип request body (из @RequestBody) — fully qualified class name
   - Тип response (из ResponseEntity<...> или из @return) — fully qualified
   - Параметры пути (@PathVariable) и query (@RequestParam) — список имён
   - Security: @PreAuthorize / @Secured / @RolesAllowed — текст аннотации
4. Если в проекте есть OpenAPI/Swagger (springdoc) — отметь, что описание API
   также доступно через /v3/api-docs или /swagger-ui.

Не читай тестовые контроллеры (src/test/java).
Не разворачивай тела методов — нужны только сигнатуры.

**КОМПАКТНЫЙ РЕЖИМ:** если найдено >50 endpoints — НЕ выводи каждый полностью.
Выведи: счётчики по модулю, топ-3 примера на каждый модуль, и краткий список
оставшихся (только: module + method + path + handler). Без request_body_type
и response_type для "лёгких" эндпоинтов.

Верни JSON ровно такой структуры:
{
  "openapi_available": true|false,
  "openapi_path": "/v3/api-docs" | null,
  "endpoints_count_by_module": {"foo": N, ...},
  "endpoints_total": N,
  "endpoints": [
    {
      "module": "proxy-service",
      "controller_class": "com.example.proxy.controller.UserController",
      "http_method": "GET",
      "path": "/api/v1/users/{id}",
      "handler": "getUser",
      "path_params": ["id"],
      "query_params": ["includeDeleted"],
      "request_body_type": null,
      "response_type": "com.example.proxy.dto.UserResponse",
      "security": "@PreAuthorize(\"hasRole('USER')\")"
    }
  ]
}
```

---

## 3.2. async-mapper

```
description: "Map Kafka/RabbitMQ producers and consumers"
subagent_type: general-purpose

prompt:
Собери каталог асинхронных интеграций.

Корень проекта: <root>
Модули: <module list>

Шаги:
1. Найди Kafka consumers:
   grep -rE '@KafkaListener' <module>/src/main/java
   Для каждого извлеки: topics (atom или массив), groupId, имя метода, тип
   payload (из аргумента метода).
2. Найди Kafka producers:
   grep -rE 'KafkaTemplate.*\.send' <module>/src/main/java
   Для каждой точки отправки извлеки: topic (если хардкод или константа),
   тип сообщения, класс-источник, метод-источник.
3. Найди Spring Cloud Stream (если есть):
   grep -rE '@(StreamListener|Input|Output)' / @EnableBinding.
4. RabbitMQ:
   - Consumers: @RabbitListener (queues=...)
   - Producers: RabbitTemplate.convertAndSend(...)
5. JMS (если в build есть spring-jms):
   - @JmsListener, JmsTemplate.send
6. Не читай тела методов глубоко — только верхний уровень.

Верни JSON:
{
  "kafka": {
    "consumers": [
      {
        "module": "proxy-service",
        "class": "com.example.UserEventConsumer",
        "method": "onUserCreated",
        "topics": ["user.created", "user.updated"],
        "group_id": "proxy-service-users",
        "payload_type": "com.example.event.UserEvent"
      }
    ],
    "producers": [
      {
        "module": "proxy-service",
        "class": "com.example.OrderService",
        "method": "publishOrderCreated",
        "topic": "order.created",
        "payload_type": "com.example.event.OrderEvent"
      }
    ]
  },
  "rabbit": { "consumers": [...], "producers": [...] },
  "jms": { "consumers": [...], "producers": [...] }
}

Пустые секции опускай (если RabbitMQ нет — секции "rabbit" нет).
```

---

## 3.3. domain-mapper

```
description: "Map JPA entities and relations"
subagent_type: general-purpose

prompt:
Собери доменную модель (JPA-сущности).

Корень проекта: <root>
Модули: <module list>

Шаги:
1. Найди все @Entity и @MappedSuperclass:
   grep -rE '@(Entity|MappedSuperclass|Embeddable)' <module>/src/main/java
2. Для каждой entity извлеки:
   - Имя класса (короткое и FQCN)
   - Имя таблицы (из @Table(name=...) или дефолт = имя класса)
   - Поля с типами (без геттеров/сеттеров)
   - @Id поле и тип
   - Связи:
     * @OneToMany(mappedBy=...) — куда указывает
     * @ManyToOne(targetEntity, @JoinColumn) — на какую entity
     * @OneToOne, @ManyToMany — аналогично
   - Embedded (@Embedded на поле + @Embeddable на классе)
   - Inheritance (@Inheritance(strategy=...))
3. Поля игнорируй: transient (@Transient), статические, finals со значениями.

Не читай тела методов entity (геттеры/сеттеры/equals).

**КОМПАКТНЫЙ РЕЖИМ:** если найдено >30 entities — НЕ выводи каждое поле.
Выведи: для каждой entity только имя, FQCN, extends-родителя, count полей и
3-5 ключевых полей (PK + 2-3 показательных). Связи — да, всегда. Embeddables
— список имён.

Верни JSON:
{
  "entities_count_by_module": {...},
  "entities_total": N,
  "entities": [
    {
      "module": "proxy-service",
      "class": "User",
      "fqcn": "com.example.proxy.entity.User",
      "table": "users",
      "id_field": "id",
      "id_type": "Long",
      "fields": [
        {"name": "email", "type": "String", "nullable": false},
        {"name": "createdAt", "type": "Instant", "nullable": false}
      ],
      "relations": [
        {"type": "OneToMany", "field": "orders", "target": "Order", "mapped_by": "user"}
      ],
      "embedded": [
        {"field": "address", "target": "Address"}
      ]
    }
  ],
  "embeddables": [
    {"class": "Address", "fields": [{"name": "city", "type": "String"}, ...]}
  ]
}
```

---

## 3.4. db-mapper

```
description: "Reconstruct DB schema from migrations"
subagent_type: general-purpose

prompt:
Восстанови финальную схему БД из миграций.

Корень проекта: <root>
Модули: <module list>

Шаги:
1. Найди папки с миграциями:
   - Flyway: src/main/resources/db/migration/V*.sql или R*.sql
   - Liquibase: src/main/resources/db/changelog/db.changelog-master.{xml,yaml}
     + дочерние changeset-файлы
2. Если нет ни того ни другого — верни {"present": false, "tool": null} и стоп.
3. Прочитай миграции В ХРОНОЛОГИЧЕСКОМ ПОРЯДКЕ (по имени файла для Flyway, по
   включению для Liquibase).
4. Симулируй применение: построй финальную схему. Учти:
   - CREATE TABLE — создаёт.
   - ALTER TABLE ADD COLUMN — добавляет.
   - ALTER TABLE DROP COLUMN — удаляет.
   - DROP TABLE — удаляет таблицу.
   - CREATE INDEX — добавляет индекс.
   - FK через REFERENCES или ADD CONSTRAINT.
5. НЕ запускай SQL и НЕ подключайся к БД. Это статический анализ файлов.

Верни JSON:
{
  "present": true,
  "tool": "flyway" | "liquibase",
  "migrations_count": 47,
  "schemas": ["public", "audit"],
  "tables": [
    {
      "schema": "public",
      "name": "users",
      "columns": [
        {"name": "id", "type": "BIGINT", "nullable": false, "default": null, "is_pk": true},
        {"name": "email", "type": "VARCHAR(255)", "nullable": false, "default": null, "is_pk": false}
      ],
      "indexes": [
        {"name": "idx_users_email", "columns": ["email"], "unique": true}
      ],
      "foreign_keys": [
        {"name": "fk_users_org", "column": "org_id", "references_table": "organizations", "references_column": "id"}
      ]
    }
  ]
}

Если parsing какой-то конкретной миграции зафейлен (сложный нестандартный SQL,
storedproc) — пропусти её, но добавь в "skipped" список с файлом и причиной.
```

---

## 3.5. integration-mapper

```
description: "Map external HTTP/SDK integrations"
subagent_type: general-purpose

prompt:
Собери исходящие интеграции (что сервис зовёт наружу).

Корень проекта: <root>
Модули: <module list>

Шаги:
1. Feign-клиенты:
   grep -rE '@FeignClient' <module>/src/main/java
   Извлеки: имя сервиса (name=), url (если хардкод), методы с их эндпоинтами.
2. WebClient/RestTemplate:
   grep -rE '(WebClient\.create|new RestTemplate|RestTemplate\(\))' <module>/src/main/java
   Найди классы-обёртки (типа SomeApiClient). Для каждого вытащи URL из
   конфигов (см. config-mapper) и методы.
3. Spring Cloud OpenFeign-аналоги.
4. Известные SDK по импортам (примеры — не исчерпывающе):
   - com.amazonaws.*  → AWS SDK
   - com.google.cloud.* → GCP SDK
   - software.amazon.awssdk.* → AWS SDK v2
   - io.minio.* → MinIO
   - org.elasticsearch.* / co.elastic.* → Elasticsearch
   - redis.clients.jedis.* / org.springframework.data.redis.* → Redis
5. gRPC: grep '@GrpcClient' или сгенерированные Stub-классы.

Верни JSON:
{
  "feign_clients": [
    {
      "module": "proxy-service",
      "class": "com.example.UserApiClient",
      "name": "user-service",
      "url": "${user.service.url}" | "https://...",
      "methods": [
        {"method": "GET", "path": "/users/{id}", "java_method": "getUser"}
      ]
    }
  ],
  "http_clients": [
    {"module": "proxy-service", "class": "PaymentClient", "type": "WebClient",
     "base_url_property": "payment.base-url", "methods": ["charge", "refund"]}
  ],
  "sdks": [
    {"name": "AWS S3", "imports": ["com.amazonaws.services.s3.AmazonS3"],
     "usage_classes": ["com.example.FileStorageService"]}
  ],
  "grpc_clients": []
}
```

---

## 3.6. config-mapper

```
description: "Map configuration profiles and key properties"
subagent_type: general-purpose

prompt:
Собери конфигурацию проекта по профилям.

Корень проекта: <root>
Модули: <module list>

Шаги:
1. Для каждого модуля найди:
   - src/main/resources/application.yml / application.yaml / application.properties
   - src/main/resources/application-*.yml — профильные
   - bootstrap.yml (Spring Cloud)
2. Прочитай каждый файл. Извлеки:
   - Имена профилей (из имён файлов: application-prod.yml → prod).
   - Верхнеуровневые ключи (spring, server, logging, app, custom).
3. Для основных секций собери ключевые свойства:
   - server.port
   - spring.datasource.url (только показывай как переменную ${...}, не значение)
   - spring.kafka.bootstrap-servers (то же)
   - spring.profiles.active
   - logging.level.*
   - feature.* / app.feature.* — фича-флаги
4. Постройте diff: какие свойства отличаются между профилями. Не сравнивай
   секретные значения (используй placeholders вида ${...}), только КЛЮЧИ.

НЕ извлекай реальные значения секретов (пароли, токены, ключи). Помечай как "***".

Верни JSON:
{
  "modules_with_config": ["proxy-service", "springproxy"],
  "profiles": ["default", "dev", "prod", "test"],
  "key_properties": [
    {
      "module": "proxy-service",
      "profile": "default",
      "props": {
        "server.port": "8080",
        "spring.datasource.url": "${DB_URL}",
        "spring.kafka.bootstrap-servers": "${KAFKA_BROKERS}"
      }
    }
  ],
  "feature_flags": [
    {"key": "app.feature.new-checkout", "profiles": {"dev": "true", "prod": "false"}}
  ],
  "profile_diff": [
    {"property": "server.port", "values": {"dev": "8080", "prod": "9090"}}
  ]
}
```

---

## 3.7. cross-cutting-mapper

```
description: "Map filters, AOP aspects, scheduled tasks, event listeners"
subagent_type: general-purpose

prompt:
Собери сквозные механизмы: фильтры, AOP-аспекты, scheduled, application events.

Корень проекта: <root>
Модули: <module list>

Шаги:
1. Фильтры:
   - Классы, extends OncePerRequestFilter
   - Классы, implements Filter
   - @WebFilter
2. Интерсепторы Spring MVC:
   - implements HandlerInterceptor
3. AOP-аспекты:
   - @Aspect annotation
   - Для каждого @Before/@After/@Around — pointcut выражение и имя метода.
4. Scheduled tasks:
   - @Scheduled методы. Извлеки cron или fixedRate/fixedDelay.
5. Application events:
   - @EventListener методы. Тип события из аргумента.
   - ApplicationEventPublisher.publishEvent(...) — где публикуется.
6. Security configuration:
   - Классы extends WebSecurityConfigurerAdapter (устаревший) или с
     @EnableWebSecurity + @Bean SecurityFilterChain.

Не читай тела методов глубоко. Имя + 1-2 строчки реализации = достаточно для
понимания цели.

Верни JSON:
{
  "filters": [
    {"module": "proxy-service", "class": "AuthFilter",
     "order": 1, "purpose": "Проверяет JWT в Authorization header"}
  ],
  "interceptors": [
    {"module": "proxy-service", "class": "AuditInterceptor", "purpose": "..."}
  ],
  "aspects": [
    {"module": "proxy-service", "class": "MetricsAspect",
     "advice": "@Around", "pointcut": "@within(MetricsTracked)",
     "purpose": "Замеряет время выполнения помеченных методов"}
  ],
  "scheduled": [
    {"module": "proxy-service", "class": "CleanupJob", "method": "deleteOldRecords",
     "schedule": "cron: 0 0 3 * * *",  "purpose": "Удаление записей старше 30 дней"}
  ],
  "event_listeners": [
    {"module": "proxy-service", "class": "OrderEventListener",
     "method": "onOrderCreated", "event_type": "OrderCreatedEvent",
     "purpose": "Отправка уведомления в Kafka после создания заказа"}
  ],
  "event_publishers": [
    {"module": "proxy-service", "class": "OrderService",
     "publishes": ["OrderCreatedEvent", "OrderCancelledEvent"]}
  ],
  "security_config": [
    {"module": "proxy-service", "class": "SecurityConfig",
     "summary": "JWT-based auth, всё под /api/** требует ROLE_USER"}
  ]
}
```

---

## 3.8. use-case-mapper

```
description: "Extract top use cases with call sequences"
subagent_type: general-purpose

prompt:
Найди топ-5 типичных use case в проекте и для каждого собери последовательность
вызовов (controller → service → repository / cache / external client).

Корень проекта: <root>
Модули: <module list>

Шаги:
1. Просмотри все контроллеры. Отбери 5 эндпоинтов, которые покажут систему
   наиболее показательно:
   - Предпочитай "create" / "process" операции (POST с body) — они обычно идут
     через больше слоёв, чем "list/get".
   - Один представитель на каждый ключевой домен (e.g., один artifact-create,
     один contract-create, один cache-read).
   - Избегай явных прокси-форвардеров, если они просто дублируют исходный
     контроллер — выбирай "толстую" версию.
   - Включай разнообразие путей: один с кэшем, один с внешним вызовом, один с
     транзакцией БД.
2. Для каждого выбранного эндпоинта проследи цепочку:
   - Контроллер: какой сервис зовёт, с какими параметрами.
   - Сервис: какие репозитории/кэши/клиенты использует, что делает с данными.
   - Репозиторий: какие методы Spring Data / native query.
   - Кэш: get/put/invalidate?
   - Внешние клиенты: какой URL зовёт.
3. Для каждого use case — описание + список шагов в порядке выполнения.

Не пиши код. Не делай выводов о бизнес-логике, если она неочевидна из имён.

Верни JSON:
{
  "use_cases": [
    {
      "id": 1,
      "title": "Создание артефакта",
      "trigger": {"module": "...", "controller": "...", "endpoint": "POST /api/v2/artifact/new"},
      "request_payload": "...",
      "response": "...",
      "steps": [
        {"actor": "Controller", "action": "ArtifactController#addNewArtifact", "calls": "ArtifactService#save"},
        {"actor": "Service", "action": "ArtifactService#save", "calls": "ArtifactRepository#save"},
        {"actor": "Service", "action": "ArtifactService#save", "calls": "ArtifactCacheService#put"},
        {"actor": "Repository", "action": "ArtifactRepository#save", "calls": "JPA persist"},
        {"actor": "Cache", "action": "ArtifactCacheService#put", "calls": "RedisTemplate.opsForValue().set"}
      ],
      "notes": "Транзакция на уровне сервиса. Кэш обновляется ПОСЛЕ персиста."
    }
  ]
}
```

---

## 3.9. glossary-mapper

```
description: "Build domain glossary with differentiation"
subagent_type: general-purpose

prompt:
Собери глоссарий доменных терминов проекта.

Корень проекта: <root>
Модули: <module list>

Шаги:
1. Собери все классы из пакетов `model`, `entity`, `dto`, `domain`, `enum`,
   `event` (исключая `controller`, `service`, `config`, `repository`).
2. Для каждого класса:
   - Краткое описание (выведи из имени + полей + использований).
   - Поля (топ-5 ключевых).
   - Где используется (в каких сервисах/контроллерах появляется как параметр или
     возвращаемое значение).
3. **Группируй похожие термины.** Если есть `Artifact`, `Document`,
   `DynamicDocument` — это, вероятно, связанные понятия. Найди их различия:
   - У какого какие поля есть, а у какого нет?
   - Какие use case каждый покрывает (по эндпоинтам)?
   - Какие отношения между ними (parent/child, наследование)?
4. Выдели **enum-типы и константы** — это словари статусов/типов.

Не выдумывай определения. Если из кода неясно — пометь `[неясно из кода]`.

Верни JSON:
{
  "terms": [
    {
      "name": "Artifact",
      "fqcn": "com.storage...Artifact",
      "kind": "entity",
      "summary": "Хранимая единица с произвольным JSON-payload, поддерживает иерархию parent/children, ассоциирована с Employee.",
      "key_fields": ["name", "surname", "payload (jsonb)", "parent", "employee"],
      "used_in": ["ArtifactController", "ArtifactService"]
    }
  ],
  "term_groups": [
    {
      "group_name": "Документы (Document family)",
      "terms": ["Document", "Contract", "Insurance", "DynamicDocument"],
      "differentiation": "Document — абстрактная сущность с name/surname/createDateTime. Contract и Insurance — её JOINED-наследники с одним типизированным полем (contractText / vehicleType). DynamicDocument — параллельная иерархия без общего родителя с Document, поля задаются в рантайме через DynamicFieldInfoStr."
    }
  ],
  "enums": [
    {"name": "OrderStatus", "values": ["NEW", "PAID", "SHIPPED"], "used_in": ["..."]}
  ]
}
```

---

## 3.10. operations-mapper

```
description: "Map deployment, env, monitoring"
subagent_type: general-purpose

prompt:
Собери операционный срез проекта.

Корень проекта: <root>
Модули: <module list>

Шаги:
1. **Контейнеризация:**
   - Dockerfile (FROM base, ports EXPOSE, ENTRYPOINT, copy steps).
   - compose.yaml / docker-compose.yml — какие сервисы вместе, depends_on,
     volumes, env_file/environment, healthcheck.
   - Kubernetes/Helm если есть (deployment.yaml, values.yaml).
2. **Build-плагины:**
   - В build.gradle: jacoco, spotless, checkstyle, springBootRun, custom tasks.
   - Что доступно через `./gradlew <task>`.
3. **Env-переменные:**
   - Грепай `${...:...}` в application.yml — это ожидаемые env vars.
   - Группируй: БД, кэш, очереди, внешние сервисы, фича-флаги.
4. **Actuator / health endpoints:**
   - `management.endpoints.web.exposure.*` — какие открыты.
   - Если включены — какие профили их экспонируют.
5. **Логирование:**
   - logback.xml / logback-spring.xml — формат, аппендеры, уровни.
6. **Скрипты:**
   - scripts/ если есть — что и для чего.
   - README.md проекта — есть ли инструкции по запуску.
7. **Базовые образы:**
   - Из Dockerfile определи runtime (Eclipse Temurin? Amazon Corretto?).

Не выдумывай. Если файла нет — фиксируй "не найдено".

Верни JSON:
{
  "containerization": {
    "dockerfile": {"path": "Dockerfile", "base_image": "...", "exposed_ports": [...], "entrypoint": "..."},
    "compose": {"path": "compose.yaml", "services": [{"name": "...", "image": "...", "depends_on": [...], "ports": [...]}]}
  },
  "gradle_tasks": ["bootRun", "build", "test", "jacocoTestReport"],
  "env_vars": [
    {"name": "DB_URL", "default": "...", "category": "database", "used_in": ["application.yml"]},
    {"name": "REDIS_HOST", "default": "localhost", "category": "cache", "used_in": [...]}
  ],
  "actuator": {
    "enabled": true|false,
    "exposed_in_profiles": ["docker"],
    "endpoints": ["health", "info", "metrics"]
  },
  "logging": {
    "config_file": "logback.xml" | null,
    "format": "...",
    "appenders": ["CONSOLE", "FILE"]
  },
  "scripts": [{"path": "scripts/init.sh", "purpose": "..."}],
  "readme_quickstart": "Есть/нет, ключевые команды если есть"
}
```
