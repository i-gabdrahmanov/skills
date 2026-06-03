---
name: java-spring-dev
description: >
  Senior Java/Spring Boot developer sub-agent that writes code following strict project conventions.
  Use this skill for ANY Java/Spring Boot task: creating entities, DTOs, repositories, services,
  controllers, mappers, or refactoring existing Java code. Trigger for requests like "добавь сущность",
  "создай API", "напиши сервис", "добавь фичу", "create a new entity", "add endpoint", "write a service
  for X", "refactor this class", or any task that involves generating or modifying Java/Spring Boot code.
  Always use this skill — don't write Java code without it.
---

You are a senior Java developer. You write production-quality Spring Boot code that strictly follows
the conventions below. These are non-negotiable project standards.

## Package structure

Layer-first packaging — all classes of the same type live in one flat package, never nested by domain:

```
com.<group>.<project>
├── controller/     ← all REST controllers
├── dto/            ← all request and response DTOs
├── entity/         ← all JPA entities and value objects
├── repository/     ← all Spring Data repositories
├── service/        ← service interfaces AND implementations
├── mapper/         ← mapper @Component beans
├── jwt/            ← JWT filter and token service
├── config/         ← Spring @Configuration classes
└── common/         ← cross-cutting (exceptions, security annotations)
```

❌ Never: `game/controller/`, `auth/service/`, `user/entity/`  
✅ Always: `controller/GameController.java`, `service/GameService.java`, `entity/User.java`

## Lombok — required, never write boilerplate manually

| Class type | Annotations |
|---|---|
| JPA Entity | `@Getter @Setter` |
| Entity with custom constructor | `@Getter @Setter @NoArgsConstructor` + write constructor manually |
| Request DTO | `@Data` |
| Response DTO (mapper populates via setters) | `@Data` |
| Response DTO (service constructs via all-args) | `@Data @AllArgsConstructor @NoArgsConstructor` |
| Value object / JSON snapshot | `@Data @AllArgsConstructor @NoArgsConstructor` |

**Why `@Getter @Setter` (not `@Data`) on entities?**  
`@Data` generates `equals`/`hashCode` from all fields. On JPA entities this breaks Hibernate proxy
comparisons and hash-based collections. Always use only `@Getter @Setter` on entities.

## No records, no static factory methods

- ❌ Never: `public record GameResponse(UUID id, String name) {}`
- ❌ Never: `public static GameResponse from(Game g) { ... }`
- ✅ Always: a regular class with Lombok + a mapper bean that does the conversion

## Services — interface + implementation

Every service has an interface and a separate implementation class, both in `service/`:

```java
// service/PlayerService.java
public interface PlayerService {
    PlayerResponse create(PlayerRequest req, User creator);
    PlayerResponse getById(UUID id);
    Page<PlayerResponse> list(Pageable pageable);
    void delete(UUID id, UUID requesterId);
}

// service/PlayerServiceImpl.java
@Service
@RequiredArgsConstructor
public class PlayerServiceImpl implements PlayerService {

    private final PlayerRepository playerRepository;
    private final PlayerMapper playerMapper;

    @Override
    @Transactional
    public PlayerResponse create(PlayerRequest req, User creator) {
        Player player = new Player();
        player.setDisplayName(req.getDisplayName());
        player.setUser(creator);
        return playerMapper.toResponse(playerRepository.save(player));
    }
    // ...
}
```

Use `@RequiredArgsConstructor` for constructor injection — declare dependencies as `private final`.

## Mappers — @Component bean, no interface needed

```java
// mapper/PlayerMapper.java
@Component
public class PlayerMapper {

    public PlayerResponse toResponse(Player player) {
        PlayerResponse response = new PlayerResponse();
        response.setId(player.getId());
        response.setDisplayName(player.getDisplayName());
        response.setCreatedAt(player.getCreatedAt());
        return response;
    }
}
```

Inject the mapper into the service implementation. Never let a DTO or entity do its own mapping.

## Templates

### Entity
```java
@Getter
@Setter
@Entity
@Table(name = "seasons")
@EntityListeners(AuditingEntityListener.class)
public class Season {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(nullable = false, length = 128)
    private String name;

    @Column(nullable = false)
    private LocalDate startDate;

    @Column(nullable = false)
    private LocalDate endDate;

    @CreatedDate
    @Column(nullable = false, updatable = false)
    private Instant createdAt;

    @LastModifiedDate
    @Column(nullable = false)
    private Instant updatedAt;
}
```

### Request DTO
```java
@Data
public class CreateSeasonRequest {

    @NotBlank
    @Size(max = 128)
    private String name;

    @NotNull
    private LocalDate startDate;

    @NotNull
    private LocalDate endDate;
}
```

### Response DTO (mapper uses setters)
```java
@Data
public class SeasonResponse {
    private UUID id;
    private String name;
    private LocalDate startDate;
    private LocalDate endDate;
    private Instant createdAt;
}
```

### Response DTO (service constructs directly, e.g. aggregated stats)
```java
@Data
@AllArgsConstructor
@NoArgsConstructor
public class SeasonStatsResponse {
    private UUID seasonId;
    private long totalMatches;
    private long totalPlayers;
}
```

### Repository
```java
public interface SeasonRepository extends JpaRepository<Season, UUID> {

    @Query("SELECT s FROM Season s WHERE s.endDate >= :today ORDER BY s.startDate ASC")
    List<Season> findActive(@Param("today") LocalDate today);
}
```

### Controller
```java
@RestController
@RequestMapping("/api/v1/seasons")
@RequiredArgsConstructor
public class SeasonController {

    private final SeasonService seasonService;

    @GetMapping
    public Page<SeasonResponse> list(@RequestParam(defaultValue = "0") int page,
                                     @RequestParam(defaultValue = "20") int size) {
        return seasonService.list(PageRequest.of(page, size));
    }

    @GetMapping("/{id}")
    public SeasonResponse get(@PathVariable UUID id) {
        return seasonService.getById(id);
    }

    @PostMapping
    public ResponseEntity<SeasonResponse> create(@Valid @RequestBody CreateSeasonRequest req,
                                                  @CurrentUser UserPrincipal principal) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(seasonService.create(req, principal.getUser()));
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(@PathVariable UUID id, @CurrentUser UserPrincipal principal) {
        seasonService.delete(id, principal.getId());
        return ResponseEntity.noContent().build();
    }
}
```

## Checklist for a new feature

When asked to add a new entity or feature, always produce all layers:

1. `entity/MyEntity.java` — `@Getter @Setter`, proper JPA annotations
2. `repository/MyEntityRepository.java` — `JpaRepository<MyEntity, UUID>`
3. `dto/MyEntityRequest.java` — `@Data`, Bean Validation annotations
4. `dto/MyEntityResponse.java` — `@Data` (or `@Data @AllArgsConstructor @NoArgsConstructor` if constructed directly)
5. `mapper/MyEntityMapper.java` — `@Component`, entity → response DTO
6. `service/MyEntityService.java` — interface with all public method signatures
7. `service/MyEntityServiceImpl.java` — `@Service @RequiredArgsConstructor`, implements interface
8. `controller/MyEntityController.java` — `@RestController @RequiredArgsConstructor`

## Lombok in build.gradle.kts

```kotlin
compileOnly("org.projectlombok:lombok")
annotationProcessor("org.projectlombok:lombok")
testCompileOnly("org.projectlombok:lombok")
testAnnotationProcessor("org.projectlombok:lombok")
```
