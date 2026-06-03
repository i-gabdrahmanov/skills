# StorageService — API & Kafka spec

- Controllers: **9**, endpoints: **20**
- Kafka topics: **0** (producers: 0, consumers: 0)

## Endpoints

| Method | Path | Handler | Params |
|---|---|---|---|
| POST | /api/v2/artifact/new | ArtifactController.addNewArtifact | body:request |
| POST | /api/v2/artifact/{count}/generate | ArtifactController.generateSomeArtifacts | path:count |
| GET | /api/v2/artifact | ArtifactController.getByJsonField | query:key, query:value |
| POST | /api/v2/artifact/json | ArtifactController.getByNativeJsonFields | body:request |
| POST | /api/v2/artifact/customFields | ArtifactController.getCustomRequestById | body:request |
| GET | /api/v2/artifact/getById | ArtifactController.getById | query:id |
| POST | /api/v2/contract/new | ContractController.addNewContract | body:dto |
| GET | /api/v2/contract | ContractController.getByName | query:name |
| POST | /api/v2/contract/{count}/generate | ContractController.generateContracts | path:count |
| POST | /api/v2/documentType/new | DocumentTypeController.addNewDocumentType | body:request |
| GET | /api/v2/documentType | DocumentTypeController.getDocumentType | query:documentTypeName |
| POST | /api/v2/dd | DynamicDocumentController.addNewDocument | body:request |
| ANY | /api/v2/dd/new | DynamicDocumentController.addNewDocument | body:request |
| POST | /api/v2/insurance/new | InsuranceController.addNewInsurance | body:dto |
| GET | /api/v2/primaryCache | PrimaryCacheController.get | query:key |
| POST | /api/v2/primaryCache/{key}/{value}/add | PrimaryCacheController.add | path:key, path:value |
| ANY | /api/v2/propertyType/new | PropertyTypeController.addNewPropertyType | body:request |
| GET | /api/v2/propertyType | PropertyTypeController.getPropertyTypeByNameAndDocType | query:propertyTypeName, body:request |
| ANY | / | RootController.root | — |
| GET | /api/v2/zk | ZkController.getConfig | — |

### ArtifactController

_File: `/Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/src/main/java/com/storage/storageservice/controller/ArtifactController.java`_

| Field | Type |
|---|---|
| service | ArtifactService |

![ArtifactController sequence](storage-spec_diagrams/diagram_01_ArtifactController.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

actor Client
participant "ArtifactController" as Ctrl
participant "ArtifactService" as ArtifactService

== POST /api/v2/artifact/new ==
Client -> Ctrl: addNewArtifact(request)
Ctrl -> ArtifactService: addNewArtifact()
ArtifactService --> Ctrl
Ctrl --> Client: void

== POST /api/v2/artifact/{count}/generate ==
Client -> Ctrl: generateSomeArtifacts(count)
Ctrl -> ArtifactService: generateSomeArtifacts()
ArtifactService --> Ctrl
Ctrl --> Client: void

== GET /api/v2/artifact ==
Client -> Ctrl: getByJsonField(key, value)
Ctrl -> ArtifactService: getArtByJsonField()
ArtifactService --> Ctrl
Ctrl --> Client: ArtifactDto

== POST /api/v2/artifact/json ==
Client -> Ctrl: getByNativeJsonFields(request)
Ctrl -> ArtifactService: getArtByNativeJsonFields()
ArtifactService --> Ctrl
Ctrl --> Client: ArtifactDto

== POST /api/v2/artifact/customFields ==
Client -> Ctrl: getCustomRequestById(request)
Ctrl -> ArtifactService: getCustomById()
ArtifactService --> Ctrl
Ctrl --> Client: ArtifactDto

== GET /api/v2/artifact/getById ==
Client -> Ctrl: getById(id)
Ctrl -> ArtifactService: getById()
ArtifactService --> Ctrl
Ctrl --> Client: ArtifactDto

@enduml
```

</details>

### ContractController

_File: `/Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/src/main/java/com/storage/storageservice/controller/ContractController.java`_

| Field | Type |
|---|---|
| contractService | ContractService |

![ContractController sequence](storage-spec_diagrams/diagram_02_ContractController.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

actor Client
participant "ContractController" as Ctrl
participant "ContractService" as ContractService

== POST /api/v2/contract/new ==
Client -> Ctrl: addNewContract(dto)
Ctrl -> ContractService: addNewContract()
ContractService --> Ctrl
Ctrl --> Client: Void

== GET /api/v2/contract ==
Client -> Ctrl: getByName(name)
Ctrl -> ContractService: findByName()
ContractService --> Ctrl
Ctrl --> Client: ContractDto

== POST /api/v2/contract/{count}/generate ==
Client -> Ctrl: generateContracts(count)
Ctrl -> ContractService: generateContracts()
ContractService --> Ctrl
Ctrl --> Client: Void

@enduml
```

</details>

### DocumentTypeController

_File: `/Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/src/main/java/com/storage/storageservice/controller/DocumentTypeController.java`_

| Field | Type |
|---|---|
| service | DocumentTypeService |

![DocumentTypeController sequence](storage-spec_diagrams/diagram_03_DocumentTypeController.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

actor Client
participant "DocumentTypeController" as Ctrl
participant "DocumentTypeService" as DocumentTypeService

== POST /api/v2/documentType/new ==
Client -> Ctrl: addNewDocumentType(request)
Ctrl -> DocumentTypeService: addDocumentType()
DocumentTypeService --> Ctrl
Ctrl --> Client: void

== GET /api/v2/documentType ==
Client -> Ctrl: getDocumentType(documentTypeName)
Ctrl -> DocumentTypeService: getDocumentByName()
DocumentTypeService --> Ctrl
Ctrl --> Client: DocumentTypeDto

@enduml
```

</details>

### DynamicDocumentController

_File: `/Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/src/main/java/com/storage/storageservice/controller/DynamicDocumentController.java`_

| Field | Type |
|---|---|
| service | DynamicDocumentService |

![DynamicDocumentController sequence](storage-spec_diagrams/diagram_04_DynamicDocumentController.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

actor Client
participant "DynamicDocumentController" as Ctrl
participant "DynamicDocumentService" as DynamicDocumentService

== POST /api/v2/dd ==
Client -> Ctrl: addNewDocument(request)
Ctrl -> DynamicDocumentService: addNewDocument()
DynamicDocumentService --> Ctrl
Ctrl --> Client: void

== ANY /api/v2/dd/new ==
Client -> Ctrl: addNewDocument(request)
Ctrl -> DynamicDocumentService: addNewDocument()
DynamicDocumentService --> Ctrl
Ctrl --> Client: void

@enduml
```

</details>

### InsuranceController

_File: `/Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/src/main/java/com/storage/storageservice/controller/InsuranceController.java`_

| Field | Type |
|---|---|
| insuranceService | InsuranceService |

![InsuranceController sequence](storage-spec_diagrams/diagram_05_InsuranceController.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

actor Client
participant "InsuranceController" as Ctrl
participant "InsuranceService" as InsuranceService

== POST /api/v2/insurance/new ==
Client -> Ctrl: addNewInsurance(dto)
Ctrl -> InsuranceService: newInsurance()
InsuranceService --> Ctrl
Ctrl --> Client: Void

@enduml
```

</details>

### PrimaryCacheController

_File: `/Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/src/main/java/com/storage/storageservice/controller/PrimaryCacheController.java`_

| Field | Type |
|---|---|
| primaryCacheService | CacheService |

![PrimaryCacheController sequence](storage-spec_diagrams/diagram_06_PrimaryCacheController.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

actor Client
participant "PrimaryCacheController" as Ctrl
participant "CacheService" as CacheService

== GET /api/v2/primaryCache ==
Client -> Ctrl: get(key)
Ctrl -> CacheService: get()
CacheService --> Ctrl
Ctrl --> Client: String

== POST /api/v2/primaryCache/{key}/{value}/add ==
Client -> Ctrl: add(key, value)
Ctrl -> CacheService: set()
CacheService --> Ctrl
Ctrl --> Client: void

@enduml
```

</details>

### PropertyTypeController

_File: `/Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/src/main/java/com/storage/storageservice/controller/PropertyTypeController.java`_

| Field | Type |
|---|---|
| propertyTypeService | PropertyTypeService |

![PropertyTypeController sequence](storage-spec_diagrams/diagram_07_PropertyTypeController.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

actor Client
participant "PropertyTypeController" as Ctrl
participant "PropertyTypeService" as PropertyTypeService

== ANY /api/v2/propertyType/new ==
Client -> Ctrl: addNewPropertyType(request)
Ctrl -> PropertyTypeService: addPropertyType()
PropertyTypeService --> Ctrl
Ctrl --> Client: void

== GET /api/v2/propertyType ==
Client -> Ctrl: getPropertyTypeByNameAndDocType(propertyTypeName, request)
Ctrl -> PropertyTypeService: getPropertyTypeByNameAndDocType()
PropertyTypeService --> Ctrl
Ctrl --> Client: PropertyTypeDto

@enduml
```

</details>

### RootController

_File: `/Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/src/main/java/com/storage/storageservice/controller/RootController.kt`_

![RootController sequence](storage-spec_diagrams/diagram_08_RootController.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

actor Client
participant "RootController" as Ctrl

== ANY / ==
Client -> Ctrl: root()
Ctrl --> Client: Void

@enduml
```

</details>

### ZkController

_File: `/Users/iskandergabdrahmanov/Documents/dev/StorageService/StorageService/src/main/java/com/storage/storageservice/controller/ZkController.java`_

| Field | Type |
|---|---|
| zkConfigWatcher | ZkConfigWatcher |

![ZkController sequence](storage-spec_diagrams/diagram_09_ZkController.svg)

<details><summary>PlantUML source</summary>

```plantuml
@startuml
skinparam responseMessageBelowArrow true

actor Client
participant "ZkController" as Ctrl
participant "ZkConfigWatcher" as ZkConfigWatcher

== GET /api/v2/zk ==
Client -> Ctrl: getConfig()
Ctrl -> ZkConfigWatcher: getAppParamsConfig()
ZkConfigWatcher --> Ctrl
Ctrl --> Client: String

@enduml
```

</details>
