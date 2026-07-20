<!-- ppt-master-schema: spec-lock/v1 -->
# Execution Lock

## canvas
- viewBox: 0 0 900 383
- format: WeChat Article Header

## communication
- audience: Researchers and open-source readers scanning the first page of the Argus technical report
- communication_intent: Explain the recurrent Argus runtime and summarize its strongest cross-task results without mixing incompatible units
- audience_outcome: Understand the role loop, persistent shared state, and breadth of measured outcomes in under one minute
- core_message: Argus repeatedly plans, executes, reviews, and retains reusable state; seven task-native result cards summarize the measured outcomes
- delivery_context: Reader-led, embedded at full text width on the paper's first page
- artifact_afterlife: Editable PPTX source, vector paper figure, archive, and future author hand-off
- consumption_mode: text

## mode
- mode: briefing

## visual_style
- visual_style: custom
- visual_style_behavior: Flat editorial panels with crisp rules, restrained rounded corners, lightly hand-drawn native-vector anime role markers, compact data-journalism chart labels, asymmetric 4+3 card rhythm, generous micro-spacing, no shadows, and no decorative raster imagery.

## colors
- bg: #FBF7EE
- surface: #FFFDF8
- primary: #24465D
- deep_ink: #173B70
- accent: #315BCE
- positive: #287D70
- authority: #C38A20
- text: #24465D
- muted: #66717D
- grid: #D8E0E8

## typography
- font_family: Arial, Microsoft YaHei, sans-serif
- body: 15
- title: 20
- subtitle: 16
- lead: 18
- annotation: 12
- footnote: 10

## icons
- library: custom-native
- inventory: manager-avatar, planner-avatar, engineer-avatar, reviewer-avatar

## page_rhythm
- P01: anchor

## pptx_structure
- mode: flat

## forbidden
- Mixing icon libraries
- `mask`, `<style>`, `class`, external CSS, `<foreignObject>`, `textPath`, `@font-face`, `<animate*>`, `<set>`, `<script>` / event attributes, `<iframe>`
- HTML named entities in text; write typography as raw Unicode and escape XML reserved characters
