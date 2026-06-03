---
description: >
  End-to-end pipeline для реализации новой фичи: BRD → тех-дизайн → Jira → код → тесты → PR.
  Вызывай как /feature-pipeline [идея или JIRA-KEY]. Контент sub-скиллов инжектируется
  при загрузке команды — без зависимости от Skill() tool.
---

<!-- Контент sub-скиллов инжектируется при загрузке команды -->

<brd-interview-instructions>
!{cat ~/.gigacode/skills/brd-interview/SKILL.md}
</brd-interview-instructions>

<business-requirements-instructions>
!{cat ~/.gigacode/skills/business-requirements/SKILL.md}
</business-requirements-instructions>

<system-analyst-instructions>
!{cat ~/.gigacode/skills/system-analyst/SKILL.md}
</system-analyst-instructions>

<tech-design-instructions>
!{cat ~/.gigacode/skills/tech-design/SKILL.md}
</tech-design-instructions>

<jira-task-writer-instructions>
!{cat ~/.gigacode/skills/jira-task-writer/SKILL.md}
</jira-task-writer-instructions>

<java-spring-dev-instructions>
!{cat ~/.gigacode/skills/java-spring-dev/SKILL.md}
</java-spring-dev-instructions>

---

<!-- Главный оркестратор -->

!{cat ~/.gigacode/skills/feature-pipeline/SKILL.md}

---

ВАЖНО: В этой команде все sub-скиллы уже загружены выше в тегах <X-instructions>.
Когда оркестратор говорит `read_file("~/.gigacode/skills/X/SKILL.md")` — не нужно читать файл,
инструкции уже есть в контексте. Просто следуй им.
