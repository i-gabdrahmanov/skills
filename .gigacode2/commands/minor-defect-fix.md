---
description: >
  End-to-end pipeline для устранения минорного дефекта из Jira. Вызывай как
  /minor-defect-fix [JIRA-KEY]. Контент sub-скиллов инжектируется при загрузке
  команды — без зависимости от Skill() tool.
---

<!-- Контент sub-скиллов инжектируется здесь при загрузке команды -->

<defect-analyzer-instructions>
!{cat ~/.gigacode/skills/defect-analyzer/SKILL.md}
</defect-analyzer-instructions>

<bugfix-developer-instructions>
!{cat ~/.gigacode/skills/bugfix-developer/SKILL.md}
</bugfix-developer-instructions>

<java-spring-dev-instructions>
!{cat ~/.gigacode/skills/java-spring-dev/SKILL.md}
</java-spring-dev-instructions>

---

<!-- Главный оркестратор — выполняй инструкции ниже, используя секции выше вместо вызовов Skill() -->

!{cat ~/.gigacode/skills/minor-defect-fix/SKILL.md}

---

ВАЖНО: В этой команде sub-скиллы уже загружены выше в тегах
<defect-analyzer-instructions>, <bugfix-developer-instructions>, <java-spring-dev-instructions>.
Когда оркестратор говорит `read_file("~/.gigacode/skills/X/SKILL.md")` — не нужно читать файл,
инструкции уже есть в контексте выше. Просто следуй им.
