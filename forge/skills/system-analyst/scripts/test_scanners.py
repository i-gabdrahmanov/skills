#!/usr/bin/env python3
"""Тесты детерминированных сканеров system-analyst на фикстуре-мини-проекте.

Скан — ground truth всего грундинга и судей; раньше тесты были только в feature-pipeline.
Проверяем: domain (@Entity), api (endpoint), reuse (dependency + util-класс).

Требует Python 3.10+.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import db  # noqa: E402
import domain  # noqa: E402
import endpoints  # noqa: E402
import integration  # noqa: E402
import kafka  # noqa: E402
import reuse  # noqa: E402
import scan_all  # noqa: E402

BUILD_GRADLE = """
dependencies {
  implementation 'org.apache.commons:commons-lang3:3.14.0'
  implementation("org.springframework.boot:spring-boot-starter-web:3.2.1")
  testImplementation 'org.junit.jupiter:junit-jupiter:5.10'
}
"""

ENTITY_JAVA = """
package com.x.domain;
import javax.persistence.Entity;
@Entity
public class Artifact { private Long id; }
"""

CONTROLLER_JAVA = """
package com.x.api;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/v1")
public class ArtifactController {
  @GetMapping("/artifacts")
  public String list() { return ""; }
}
"""

UTIL_JAVA = """
package com.x.common;
public final class DateUtils {
  public static String fmt(long t) { return ""; }
  public static boolean isPast(long t) { return false; }
}
"""

SERVICE_JAVA = """
package com.x.service;
public class ArtifactService { public void run() {} }
"""


class ScannerFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "build.gradle").write_text(BUILD_GRADLE, encoding="utf-8")
        base = self.root / "src/main/java/com/x"
        for sub, content in [("domain/Artifact.java", ENTITY_JAVA),
                             ("api/ArtifactController.java", CONTROLLER_JAVA),
                             ("common/DateUtils.java", UTIL_JAVA),
                             ("service/ArtifactService.java", SERVICE_JAVA)]:
            p = base / sub
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_domain_finds_entity(self):
        items = domain.scan(self.root)
        entities = [i for i in items if i.get("kind") == "entity"]
        self.assertEqual(len(entities), 1, items)
        self.assertEqual(entities[0]["name"], "Artifact")

    def test_endpoints_finds_controller(self):
        controllers = endpoints.scan(self.root)
        eps = [e for c in controllers for e in c.endpoints]
        self.assertEqual(len(eps), 1, controllers)

    def test_reuse_dependencies(self):
        deps = reuse.scan_dependencies(self.root)
        arts = {d["artifact"] for d in deps}
        self.assertIn("commons-lang3", arts)
        self.assertIn("spring-boot-starter-web", arts)
        self.assertIn("junit-jupiter", arts)

    def test_reuse_project_utils(self):
        utils = reuse.scan_project_utils(self.root)
        names = {u["class"] for u in utils}
        self.assertIn("DateUtils", names)          # util по имени + static-методы
        self.assertNotIn("ArtifactService", names)  # обычный сервис — не util
        du = next(u for u in utils if u["class"] == "DateUtils")
        self.assertTrue(any("fmt(" in m for m in du["methods"]))

    def test_scan_all_integrated(self):
        cats = scan_all.scan_root(self.root)
        self.assertIn("reuse", cats)
        self.assertEqual(cats["domain"]["gate_total"], 1)
        self.assertGreaterEqual(len(cats["reuse"]["dependencies"]), 3)
        self.assertGreaterEqual(len(cats["reuse"]["project_utils"]), 1)

    def test_test_sources_excluded(self):
        # Фикстуры из src/test не должны попадать в grounding как продакшен-артефакты.
        test_base = self.root / "src/test/java/com/x"
        for sub, content in [("domain/FakeEntity.java",
                              ENTITY_JAVA.replace("Artifact", "FakeEntity")),
                             ("api/FakeController.java",
                              CONTROLLER_JAVA.replace("ArtifactController", "FakeController"))]:
            p = test_base / sub
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        entities = [i for i in domain.scan(self.root) if i.get("kind") == "entity"]
        self.assertEqual([e["name"] for e in entities], ["Artifact"])
        eps = [e for c in endpoints.scan(self.root) for e in c.endpoints]
        self.assertEqual(len(eps), 1, "тестовый контроллер не должен считаться")

    def test_kafka_unresolved_consumer_counted(self):
        listener = """
package com.x.consumer;
import org.springframework.kafka.annotation.KafkaListener;
public class FooConsumer {
  @KafkaListener(topics = topicProvider.resolve())
  public void onMessage(String m) {}
}
"""
        p = self.root / "src/main/java/com/x/consumer/FooConsumer.java"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(listener, encoding="utf-8")
        model = kafka.scan(self.root)
        self.assertEqual(len(model.consumers), 1, "консьюмер с неразрешённым топиком должен считаться")
        self.assertEqual(model.consumers[0].topics, ["<unresolved>"])

    def test_endpoint_path_from_constants(self):
        # Путь контроллера и метода берётся из String-констант в отдельном файле + конкатенация.
        paths = """
package com.x.api;
public interface ApiPaths {
  String BASE = "/api/v1";
  String ARTIFACTS = BASE + "/artifacts";
  String LIST = "/list";
}
"""
        ctrl = """
package com.x.api;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping(ApiPaths.ARTIFACTS)
public class ArtifactCtl {
  @GetMapping(ApiPaths.LIST)
  public String list() { return ""; }
}
"""
        base = self.root / "src/main/java/com/x/api"
        (base / "ApiPaths.java").write_text(paths, encoding="utf-8")
        (base / "ArtifactCtl.java").write_text(ctrl, encoding="utf-8")
        controllers = endpoints.scan(self.root)
        ctl = next(c for c in controllers if c.class_name == "ArtifactCtl")
        self.assertEqual(ctl.base_path, "/api/v1/artifacts")
        self.assertEqual(ctl.endpoints[0].path, "/api/v1/artifacts/list")

    def test_kafka_functional_binding(self):
        cfg = """
package com.x.stream;
import org.springframework.cloud.stream.function.StreamBridge;
import java.util.function.Consumer;
import java.util.function.Function;
import java.util.function.Supplier;
import org.springframework.context.annotation.Bean;
public class StreamConfig {
  @Bean
  public Consumer<String> processOrder() { return s -> {}; }
  @Bean
  public Function<String, String> enrich() { return s -> s; }
  @Bean
  public Supplier<String> emit() { return () -> ""; }
}
"""
        p = self.root / "src/main/java/com/x/stream/StreamConfig.java"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(cfg, encoding="utf-8")
        model = kafka.scan(self.root)
        cons = {c.method for c in model.consumers}
        prod = {p.method for p in model.producers}
        self.assertIn("processOrder", cons)          # Consumer → consumer
        self.assertIn("enrich", cons)                # Function → consumer
        self.assertIn("emit", prod)                  # Supplier → producer
        self.assertIn("enrich", prod)                # Function → producer

    def test_kafka_stream_listener(self):
        src = """
package com.x.stream;
import org.springframework.cloud.stream.annotation.StreamListener;
public class OrderSink {
  @StreamListener("orders-in")
  public void handle(String m) {}
}
"""
        p = self.root / "src/main/java/com/x/stream/OrderSink.java"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
        model = kafka.scan(self.root)
        self.assertEqual(len(model.consumers), 1)
        self.assertEqual(model.consumers[0].method, "handle")

    def test_plain_consumer_bean_not_counted(self):
        # Без сигнала spring-cloud-stream обычный Consumer<>-бин НЕ должен считаться консьюмером.
        src = """
package com.x.config;
import java.util.function.Consumer;
import org.springframework.context.annotation.Bean;
public class AppConfig {
  @Bean
  public Consumer<String> auditLogger() { return s -> {}; }
}
"""
        p = self.root / "src/main/java/com/x/config/AppConfig.java"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
        model = kafka.scan(self.root)
        self.assertEqual(len(model.consumers), 0, "обычный Consumer-бин не Kafka")

    def test_integration_modern_clients(self):
        files = {
            "RestClientGw.java": """
package com.x.client;
import org.springframework.web.client.RestClient;
public class RestClientGw {
  private final RestClient rc = RestClient.builder().baseUrl("http://upz/api").build();
}
""",
            "OkHttpGw.java": """
package com.x.client;
import okhttp3.OkHttpClient;
public class OkHttpGw { private OkHttpClient http = new OkHttpClient(); }
""",
            "GrpcGw.java": """
package com.x.client;
import net.devh.boot.grpc.client.inject.GrpcClient;
public class GrpcGw {
  @GrpcClient("pricing")
  private PricingServiceGrpc.PricingBlockingStub stub;
}
""",
        }
        base = self.root / "src/main/java/com/x/client"
        base.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (base / name).write_text(content, encoding="utf-8")
        items = integration.scan(self.root)
        by_type = {i["type"] for i in items}
        self.assertIn("restclient", by_type)
        self.assertIn("okhttp", by_type)
        self.assertIn("grpc", by_type)
        rc = next(i for i in items if i["type"] == "restclient")
        self.assertEqual(rc["target"], "http://upz/api")
        grpc = next(i for i in items if i["type"] == "grpc")
        self.assertEqual(grpc["target"], "pricing")

    def test_inherited_endpoints_from_abstract_base(self):
        base = """
package com.x.api;
import org.springframework.web.bind.annotation.*;
public abstract class AbstractCrudController<T> {
  @GetMapping("/{id}")
  public T getOne(@PathVariable Long id) { return null; }
  @DeleteMapping("/{id}")
  public void remove(@PathVariable Long id) {}
}
"""
        concrete = """
package com.x.api;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/users")
public class UserController extends AbstractCrudController<User> {
  @GetMapping("/active")
  public String active() { return ""; }
}
"""
        d = self.root / "src/main/java/com/x/api"
        (d / "AbstractCrudController.java").write_text(base, encoding="utf-8")
        (d / "UserController.java").write_text(concrete, encoding="utf-8")
        controllers = endpoints.scan(self.root)
        uc = next(c for c in controllers if c.class_name == "UserController")
        paths = {(e.http_method, e.path) for e in uc.endpoints}
        self.assertIn(("GET", "/api/users/active"), paths)       # собственный
        self.assertIn(("GET", "/api/users/{id}"), paths)          # унаследованный
        self.assertIn(("DELETE", "/api/users/{id}"), paths)       # унаследованный
        # Абстрактный базовый класс сам не должен попасть в список контроллеров.
        self.assertNotIn("AbstractCrudController", {c.class_name for c in controllers})

    def test_db_yaml_liquibase_tables(self):
        changelog = """
databaseChangeLog:
  - changeSet:
      id: 1
      changes:
        - createTable:
            tableName: payment
            columns:
              - column:
                  name: id
        - addColumn:
            tableName: artifact
            columns:
              - column:
                  name: note
"""
        p = self.root / "src/main/resources/db/changelog/001-init.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(changelog, encoding="utf-8")
        res = db.scan(self.root)
        self.assertIn("payment", res["tables"])
        self.assertNotIn("artifact", res["tables"], "addColumn не создаёт таблицу")


class StripCommentsTest(unittest.TestCase):
    def test_preserves_double_slash_in_string(self):
        src = 'String url = "http://host/api"; // real comment'
        out = common.strip_comments(src)
        self.assertIn("http://host/api", out)
        self.assertNotIn("real comment", out)

    def test_strips_block_and_line(self):
        src = "/* block */ int x = 1; // trailing\nint y = 2;"
        out = common.strip_comments(src)
        self.assertNotIn("block", out)
        self.assertNotIn("trailing", out)
        self.assertIn("int x = 1;", out)
        self.assertIn("int y = 2;", out)

    def test_comment_marker_inside_string_kept(self):
        src = 'String s = "/* not a comment */ and // not either";'
        out = common.strip_comments(src)
        self.assertIn("/* not a comment */ and // not either", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
