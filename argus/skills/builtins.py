"""Bundled cross-vertical defaults and vertical-aware Skill seeding.

``argus/builtin_skills`` contains only reusable workflow skills.
Domain/vertical playbooks live under ``verticals/<name>/skills`` and are
seeded only for the active context.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)
_BUILTIN_PACKAGE = "argus.builtin_skills"
DEFAULT_PROJECT_BUILTIN_SKILLS_DIR = "argus_builtin_skills"
_BUILTIN_SEED_STATE = ".argus-builtin-seeds.json"
_MOVED_SKILL_MARKER = ".moved-from-global.json"
_LEGACY_BUILTIN_SEED_HASHES = {
    "engineer/environment-readiness.md": "f8615f2a465cbe7b2ce838179c24a575baf4fbe6370730035c85cd4dd907de9b",
    "engineer/training-infrastructure-guide.md": "43d1cbc1017173a5376f2a47642ea3ba5bf007b879ba86737514f8aba28f3f39",
    "manager/argus-manager-role.md": "dc193f31dca3acd3041544745d97b832725c0e37b55a44bd9a93db5f97a631be",
    "manager/evidence-based-stage-decision.md": "75347a834448d8abb92ae04ad486ab06c595d1fb53cbe3cd24e70b37368515ed",
    "planner/argus-planner-role.md": "30d16975503a9b41d97c05d622b4d36117677ff9500e65a4556dd2f8c244fb12",
    "reviewer/argus-reviewer-role.md": "bc971a888bfcdc3acaca939b643410f509c328376737377ba8e898f1b4dee925",
    "agent-team-lead.md": "3d7cd66367c1c093503b7985a5c86a6e35331600e746f8e96811b3ef7f3df76f",
    "curator/argus-curator-role.md": "4545b826842c39f54fa9f8f260bfb289e1d54b68403be03a9ade3a2d1172495c",
    "engineer/minimal-coding-agent.md": "18a5cbb3c6f3a937bb493eaf54db3a41637b4c57f324169a4829151105a22e73",
    "engineer/pdf-chat.md": "7debea0fb441b6b1de96dc9041266486430cded6afbffe13e7e2687acaf230e9",
    "engineer/presentation-master.md": "16fb15d865670dfaad96b0445a7aa0d410660a8f6954ce1d61d5d6df3de5f17f",
    "engineer/project-environment-management.md": "f4b1e8afa92d911b699fbb359a47bf1ad62c6a9dde7f5aaf54ed0324cdfb11c3",
    "engineer/rl-training-collapse-diagnosis.md": "6830af8a4172e28e2f7c760469a4c47955923693826e7c9dadce7e6282854f18",
    "engineer/semantic-scholar-search.md": "9d4c7db970747e4ab6f5905f64ba189f3dbb79a14d5b7d31c8ebde2c6e498e75",
    "engineer/skill-authoring-guide.md": "95babe92e841066aed028f221b0e009336422048e7461db0676b0bd40980722e",
    "engineer/stale-world-model.md": "1a461c11c40314a4ccb61bf40204198e5dfa172bfa932ad0aaed9b2165748427",
    "performance-profile-ground-truth.md": "037052a75b63a4e29b05f23652d9a8b8bb9254bc546e9f4f1e06771f11e4faa0",
    "project-venv-package-management.md": "f912af39a3a8c9914dd79abc78946fbb0b134268e560bb8977e84f9a539ac008",
    "reviewer/claim-to-code-trace.md": "6ad0934c355227eb0ec772d52cdc9849ceee20128669c437dd16e23cc9990f0b",
    "reviewer/engineer-process-audit.md": "5227066930458e945c486d628d5ff0c082c9db81e2c8d85827f94ee69c80c741",
    "reviewer/guiding-the-engineer.md": "4636cfe91844f1416de84e18155f57220b9922076274f362c2f35c0c78233d4b",
    "stale-blocker-verification-probe.md": "9c570afd9abb7ba72c194da754018c1a96ae640f70061b7669afa4021ea490e2",
}
_RETIRED_BUILTIN_SEED_HASHES = {
    # Consolidated into project-venv-package-management.md.
    "engineer/project-environment-management.md": "f4b1e8afa92d911b699fbb359a47bf1ad62c6a9dde7f5aaf54ed0324cdfb11c3",
    # Renamed on 2026-09-06 so that the library speaks like a researcher:
    # reading the evidence, the strongest argument against, claims against
    # evidence, a citation check, an environment readiness check, guiding the
    # engineer. A seeded copy under the old name is removed when it still
    # matches, archived otherwise.
    "reviewer/experiment-audit.md": (
        "67ab93f4bd258207d8eedf1f2c7b27455f50d76d746f61f07b4facd1a35dcff7"
    ),
    "reviewer/kill-argument.md": (
        "a84fba34ce27bc8d2fcaba3a245d0ca4d49c5922f8401032c71e0f468eff0150"
    ),
    "engineer/citation-audit.md": (
        "ee79f3b888dd2d60a36bd4e7efd771b345b24ca8bebf8827a4affd7d584834b2"
    ),
    "engineer/claims-evidence-audit.md": (
        "de4fbd08530256ec6ac724051cf6d37e98917b0f7b24c120fbe198e4784474ce"
    ),
    "engineer/environment-readiness-gate.md": (
        "a85d54656f5e5ffa7b5d8519456fc8e340e1df23d2f47f63a95f428bb75d7c71"
    ),
    "reviewer/reviewer-engineer-handoff.md": (
        "e31210ceaab0cf524b0edc009615bf8aaee06e9c9c77656eb1509c1037a2f800"
    ),
    "engineer/experiment-audit.md": (
        "d7fa41bfefaa0aaa8156f5febc8a4c1dc98874f3e7e24e6306f075266c49074e"
    ),
    "engineer/paper-claim-audit.md": (
        "65311eb7bc317e82195f7dcc56abf7fb8caf357f144bdd16fcf7504e48904ad1"
    ),
    "engineer/singularity-amlt-gpu-ops.md": (
        "18f7020894021a6a15a68e54022c3a7758535ce7e501cea4dc408a33f79ef6dc"
    ),
    "engineer/nanochat-autoresearch-hands-on-trace.md": (
        "7df00f9f7e985143e3ca1af53bf8d16eda864b7598ff8446034c27abefce528f"
    ),
    "engineer/nanochat-autoresearch-sota-optimization.md": (
        "ef6acedaa464fb7e9e5bac60a7737ef8590f72f00c7d658f346d3a227a893ba6"
    ),
    "engineer/nanochat-pretrain-runner.md": (
        "5986a1df8ca519f1ad4a20b9c175647922711b1bad0cf1855c0fdfa30a7d3b46"
    ),
    # Removed on 2026-10-01 after the skill usage audit (121 projects, 2026-08-28
    # to 09-30): none of these was opened by any role, or the file duplicated a
    # sibling. Seeded copies are removed when unedited, archived otherwise.
    # Research: zero-opened figure and timeline files.
    "engineer/academic-vector-figures.md": (
        "7167cd0fbd87b470ab5a9faaaffb1a457f586742cbf52101d2835c14c24c442d"
    ),
    "engineer/figure-spec.md": (
        "e63d1f7e807a3e918a79c02b5a5584ae531cc6e48de608a6fbb632ca6a16b2ca"
    ),
    "engineer/research-timeline.md": (
        "4e08e186b5cf37f6e71215cf47a0cd262a54ca574e89ed2e6c5162f616bda07a"
    ),
    # Research: the ideation sub-pattern cards, anti-patterns, companion
    # combos and the vendored attribution page; the 15 pattern cards and
    # their overview stay.
    "engineer/references/ideation/ideation-sub-patterns/C00.md": (
        "0bc790076bc61bc5a902094b3b1326375b20a2210540b9665c97728f8a869d56"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C01.md": (
        "7313de54f35bd19420a234d65261429f27555af69e6babde10bb204bd4cb82a8"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C02.md": (
        "254f83ef47ab39d36ae3475bc05ed3e0eee3c8b4f40f970c3dbec0dd5703f119"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C03.md": (
        "b0c71cc591da1a7a87579a7aaa983b8b8e1a53a9f51376dca9232ff5fd868a32"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C04.md": (
        "e6caef00b214d780e5735e4323d10aac059d5d7c8c8c8ec16a5b050f6d81ba45"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C05.md": (
        "543f82fee5c11136813c512e82bf493d6bbae00f8c857e8a0a523320e44b0dda"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C06.md": (
        "d1570fddded634848788b33c6a731dbce440d0450e6f3c550f0f517d87a7bba8"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C07.md": (
        "1cf23b65fbee15bbbffcda7f4af3542f60cc167134efce2e203cd0fc4aede69f"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C08.md": (
        "ab97e07ed950cc256732ee18991001e3f4866a23b08f04aed402a8027b4fa538"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C09.md": (
        "e178b7925e08f94311f28639a88030f84ece1468b7080707472afe432ff4126e"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C10.md": (
        "8724aaff35a9704544a9be7625e6a05d58c606335f3a65e99a825d3bbce8366a"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C11.md": (
        "65ba6973f962ab0e8e1cdea6f30da92ca27e6be3b6d08a09a1dc0e565e309e75"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C12.md": (
        "6157bfd876ba96f837c0f577399f6afc1602e419c45fe77424f57b5e446a8927"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C13.md": (
        "7e19113e0aff6d02e77fc4a9898c6c5edae5a1b20f6c46bf65cd78fae0e62d9b"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C14.md": (
        "006232a2a76a5730b6746b8caf98a3601a369af0881749e38c3c6aeec332ec1c"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C15.md": (
        "fce7d01cac2fe7f8562d5de2af87159547d7a7e9bb9b115560af14a00a6f8999"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C16.md": (
        "0978269b1da55a84902d42087c8d481a3fb40c6d299870d760ff9a64df1ed70e"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C17.md": (
        "95020d47a0cd32cd65240db80018e09119021d3616af43015b1a6797400c4303"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C18.md": (
        "871ad780b2120f7f4bd0930a3ae0d2c66e3928368a5ffbf36f4777c3fb8d01a6"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C19.md": (
        "27c570e775a3a845334dfd5d06494bfc382dbb79bad4928d349a30b9129c166a"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C20.md": (
        "44b744d5822c8c662171e2f9607e78f0dd57246938254a22354e05386841c63f"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C21.md": (
        "17fd8f2d33a83878dbd31bba149eb409be958e022ff666b52f1a8d169257b5a9"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C22.md": (
        "094fa0747a14ae5f706575ad7be75604d7ccd307420e39ad22c79bc870c5a17c"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C23.md": (
        "4f611478bb586c82f5f965e2ae8104c173355df780745af4193a721eb39ca0c1"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C24.md": (
        "b87fd8132e773ee277b2387567d6d91ff3a6680fd9e57da7591900f582a4e2ee"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C25.md": (
        "af5e435dd553c6e4893e9f17bd8da9c19e8dbf3cbc15699b0d716cf24f32b787"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C26.md": (
        "8b3b77eb217046fa5c64a7f5fd38a67bbfc96550b92cc6249a46241d222318ae"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C27.md": (
        "227cca6515c13719e55b5fd3393db32e50143795732fc252246997915eb52704"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C28.md": (
        "3930ba042b73d11820201a4e191c2203a32b837934673465d2df908adf5bbff6"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C29.md": (
        "1ad7523af2d0a5c1a2aa81e144cb6269a87fcba110397ce679604c8e3db22d9d"
    ),
    "engineer/references/ideation/ideation-sub-patterns/C30.md": (
        "07a9807ff9890c993d50f8589f02014927aa320b9e9475d240ed609da97753cd"
    ),
    "engineer/references/ideation/ideation-sub-patterns/overview.md": (
        "f38798c718bd61a1641075bbcea582ecea8c01c09756ecfd37ab86d68e720d92"
    ),
    "engineer/references/ideation/anti-patterns.md": (
        "ec3f36e3281c2a7762e3af9142323e338b0c88911d290eec331451d3c60a7c95"
    ),
    "engineer/references/ideation/ideation-patterns/companion-combos.md": (
        "eaaa98d1c54a360cf126c88345ca34c807f5206e07d0e579a36cb202592cbd86"
    ),
    "engineer/references/ideation/ATTRIBUTION.md": (
        "768eb690ea6dc36812abc8bb7bc580739e38081c2fa6efe890401d49db5445fd"
    ),
    # Software: the Verus plugin (kept in git history for the Vertical Store)
    # and the opt-in Manager grounding pass nobody enabled.
    "engineer/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md": (
        "d8b9d0454dba11e26939164ebced294115ec3441e36a732d1900c53835fde5b4"
    ),
    "engineer/formal-verification/verus-spec-generation-and-repair.md": (
        "b8b75623f4beeccd7d1d49f88c846ae581509ba500471534f828320653bb4677"
    ),
    "manager/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md": (
        "99265e73d9466208838f9537d4c72616809530d81b0066d43ac3a865845d9cf2"
    ),
    "manager/formal-verification/verus-spec-generation-and-repair.md": (
        "6fc81720d8d3d16a9b66b51179242143fb7c489362c195dfda42c778e26ab271"
    ),
    "planner/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md": (
        "0b80e1fb7dc18b9653f47a1ec40694b34d37c187bbf9ac3e6a3402f2177213fc"
    ),
    "planner/formal-verification/verus-spec-generation-and-repair.md": (
        "ca5d22fa5f52a7e619b41b438d41f26be7668e7de7cba1a69a9093af1d1803a4"
    ),
    "references/verus-spec-generation-and-repair.zh-CN.md": (
        "137e9d9e612a3f4b9a7a4d36a78da78faa03de8ef74899ca2aaa96d9cbeb2dbe"
    ),
    "reviewer/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md": (
        "f34f3985042716c286604d8dcb9a30049ec02d41cbe8a84fabbad438c39fe755"
    ),
    "reviewer/formal-verification/verus-spec-generation-and-repair.md": (
        "615c33c439f573a4a2495d9b3b4614be6e436ad014fabd87e339359d44f09103"
    ),
    "verus-spec-generation-and-repair.md": (
        "e74c0de684a224d3edc2e986c7e5e16cbe512d7ccd8558d18a2fe2ef96797614"
    ),
    "manager/software-project-grounding.md": (
        "0b1d8a2ab9b01b713568dd469bc6e44d6c59c7cc108b976e4168edbef0b44bb6"
    ),
    # Global library: identity text the prompt already carries, a diagram
    # route whose renderers are not installed, and a duplicate of
    # performance-profile-ground-truth.md.
    "engineer/drawio-diagram-authoring.md": (
        "c53d92072531bd3662ef181c7bd0459b0ecff10963a41b078e5ca6f5162b92e6"
    ),
    "engineer/mermaid-graphviz-diagrams.md": (
        "6d544cb4ec918840a3ccbe07de86771831c660c3ec07d751b3b64fd26969af12"
    ),
    "engineer/argus-engineer-role.md": (
        "3c4f983a645ed3a3be7742db797c741c1741362b93b9de72253bd5ca4b3334bf"
    ),
    "agent-md-optimize-project-template.md": (
        "04635712c232d01b2b7ecae3af0e94f32071b89996094f43ca3b89c921296eaf"
    ),
    # Kernel engineering: a single B200 kernel's work log and an external
    # reading list.
    "engineer/kernel-optimization-process-trace.md": (
        "84e8feb27c50abdeaee10413eff2147bc80cad71a52d9d5e05e0f9ac82f0cada"
    ),
    "engineer/modern-gpu-blackwell-kernel-techniques.md": (
        "e4b21b069c4714e1f41fab19186cc6452d09f87193965ad0931f27e123a11ef8"
    ),
}


#: Factory digests of the skills the seventeen community verticals shipped
#: while they were still in this package (as of the last in-tree release,
#: ``origin/dev`` 4554450cd on 2026-09-14), keyed by vertical. Workspaces
#: seeded before the split hold these bytes; where ``argus-verticals`` is
#: installed they are pruned like any other inactive vertical's seeds, where
#: it is not, nothing else enumerates them (``available_verticals()`` cannot
#: name them) and they would stay behind as matcher candidates forever.
#: Inherited built-in skills (kernelbench's kernel_engineering playbooks) are
#: deliberately absent: those still ship here.
_MOVED_VERTICAL_SEED_HASHES: dict[str, dict[str, str]] = {
    "quant": {
        "engineer/kline-chart.md": "5207776344f46c4a8772c3b0d61271f603833a172cf57f48d357b24b7ad9d423",
        "engineer/model-selection-loop.md": "f728fb01023cbe09502caea6843a66d147607eed5712cacef68e970a69509581",
        "engineer/quant-factor-loop.md": "007a427c256cb781907c823e43a1810b82bd5af3a7e851cc90a4f4495d747510",
        "reviewer/quant-factor-report-review.md": "0e993b653e5923601a3cec7b0b1e7bc10c041b4b2f69aecdfd1bab58f6f8c2d4",
    },
    "kernelbench": {
        "engineer/b200-kernelbench-runtime.md": "6d506cb21b2a820fb41791fdf07d4b74249a47d093d9980cd36a2f65dc106e83",
        "engineer/official-sol-execbench-env.md": "18804dc6aefd16b95d03166e45ea8520ec2813fc3219618e5f2d3da2ff0fe628",
        "engineer/sol-kernel-hands-on-trace.md": "e52878510d2dbb1eba8a117eb1ec2cc2647d42638f05aefccdb9bc52340feb52",
        "engineer/sol-kernel-sota-optimization.md": "2250b9c5fb8efe2a27ad0b976ce9c0abf7ded2e1680cfc9dac52391b21abb72c",
        "repair-governance-snapshot-verifier-drift.md": "3672f2f34c72e3a3c9996c62275241458e53b00fb6600e42d2fb3569ff4bebef",
        "report-only-head-to-head-benchmark-evidence.md": "6ef644420f4593f55c10297ab8e54c0beba3902cea66d48eb70c9f77917ce561",
        "sol-target-selection-without-execution.md": "8c377042931b4b1ad62bb420bfb0ecca7683bd24d368aafcf71c3b1d331bd2a6",
    },
    "speedrun": {
        "engineer/speedrun-hands-on-trace.md": "01b83b4b0a983be5a8779719dba8454f917ebfe026c23fe82f0a618321d1868c",
        "engineer/speedrun-sota-optimization.md": "0e15f62b91b452a400c7ccda928181258f179330ec7f2d89069bdc12c268aa06",
    },
    "nanogpt_speedrun": {
        "engineer/nanogpt-speedrun-h100-sota.md": "b76745e60e2d2e5ca3f844f93e7313a584b4580003ed54c4908253d5b04f30b0",
        "engineer/speedrun-hands-on-trace.md": "01b83b4b0a983be5a8779719dba8454f917ebfe026c23fe82f0a618321d1868c",
        "engineer/speedrun-sota-optimization.md": "0e15f62b91b452a400c7ccda928181258f179330ec7f2d89069bdc12c268aa06",
    },
    "chip_design": {
        "engineer/chip-design-environment-first.md": "1749dfe59ef5ea532c811de0292b4fdb9de9da886f5768217859cc766b6f1156",
        "engineer/digital-circuit-benchmark-execution.md": "de03d306d4c940693710ae07ad2e02052ef9b2875ca8487952953c520b931c5b",
        "engineer/digital-circuit-error-guided-repair.md": "23357ee12fcaa77cbd2834c289a6e4c52a4b6a1536aebed8aa68fa42eab6d629",
        "engineer/digital-circuit-first-pass-contract-closure.md": "960c56e91d6f35b8843a87bdbffd2338ca29abbc881676b0e6c3720b848584fd",
        "engineer/digital-circuit-rtl-verification.md": "ef7e355dacbf04e3ad9a9f66a217c227df60f366551b622dbbd15e2621e44a00",
        "engineer/digital-circuit-spec-guidance-registry.md": "0ad5d6e1e9774e169dfb6baae4e31fed2b22e277770d02da56badfe41397d41e",
        "reviewer/chip-design-signoff-review.md": "f031a3f469a94534eeb6ccacc297eb3b6bd1fe21cde7a181cc944bdafcf66df6",
        "reviewer/digital-circuit-benchmark-review.md": "90f49ebfc6b13c7098b252577a8a0ac451c8b94d90b89044371c98e848af33ff",
        "reviewer/digital-circuit-guidance-promotion-review.md": "2e8d431abae4953a2f32e7ba2376fd6a198755b97f5a496f18c5a2adf86e7cbb",
        "reviewer/digital-circuit-signoff-review.md": "982ff813edacd00cc38a72154f85d3f0c70150d21c1ea7606030a7ddeba3d0c9",
    },
    "digital_circuit": {
        "engineer/digital-circuit-benchmark-execution.md": "de03d306d4c940693710ae07ad2e02052ef9b2875ca8487952953c520b931c5b",
        "engineer/digital-circuit-error-guided-repair.md": "23357ee12fcaa77cbd2834c289a6e4c52a4b6a1536aebed8aa68fa42eab6d629",
        "engineer/digital-circuit-first-pass-contract-closure.md": "960c56e91d6f35b8843a87bdbffd2338ca29abbc881676b0e6c3720b848584fd",
        "engineer/digital-circuit-rtl-verification.md": "ef7e355dacbf04e3ad9a9f66a217c227df60f366551b622dbbd15e2621e44a00",
        "engineer/digital-circuit-spec-guidance-registry.md": "0ad5d6e1e9774e169dfb6baae4e31fed2b22e277770d02da56badfe41397d41e",
        "reviewer/digital-circuit-benchmark-review.md": "90f49ebfc6b13c7098b252577a8a0ac451c8b94d90b89044371c98e848af33ff",
        "reviewer/digital-circuit-guidance-promotion-review.md": "2e8d431abae4953a2f32e7ba2376fd6a198755b97f5a496f18c5a2adf86e7cbb",
        "reviewer/digital-circuit-signoff-review.md": "982ff813edacd00cc38a72154f85d3f0c70150d21c1ea7606030a7ddeba3d0c9",
    },
    "digital_circuit_benchmark": {
        "engineer/digital-circuit-benchmark-execution.md": "de03d306d4c940693710ae07ad2e02052ef9b2875ca8487952953c520b931c5b",
        "engineer/digital-circuit-error-guided-repair.md": "23357ee12fcaa77cbd2834c289a6e4c52a4b6a1536aebed8aa68fa42eab6d629",
        "engineer/digital-circuit-first-pass-contract-closure.md": "960c56e91d6f35b8843a87bdbffd2338ca29abbc881676b0e6c3720b848584fd",
        "engineer/digital-circuit-rtl-verification.md": "ef7e355dacbf04e3ad9a9f66a217c227df60f366551b622dbbd15e2621e44a00",
        "engineer/digital-circuit-spec-guidance-registry.md": "0ad5d6e1e9774e169dfb6baae4e31fed2b22e277770d02da56badfe41397d41e",
        "reviewer/digital-circuit-benchmark-review.md": "90f49ebfc6b13c7098b252577a8a0ac451c8b94d90b89044371c98e848af33ff",
        "reviewer/digital-circuit-guidance-promotion-review.md": "2e8d431abae4953a2f32e7ba2376fd6a198755b97f5a496f18c5a2adf86e7cbb",
        "reviewer/digital-circuit-signoff-review.md": "982ff813edacd00cc38a72154f85d3f0c70150d21c1ea7606030a7ddeba3d0c9",
    },
    "fiction_writing": {
        "engineer/chapter-drafting-and-continuation.md": "3d727fd292cce7190eeef96d1bec8b0607753ec859c8f0fe9ce03f525edadd98",
        "engineer/creative-brief-and-style-profile.md": "6702aee825e99f4bcdb0bea9c05c4efbed55bc2f499a18dfb865f05539d4a3cc",
        "engineer/story-and-chapter-planning.md": "a9bd8e636c30d291e3e765c5ee9fa302227700da61b619914d44a3428988f4e9",
        "engineer/story-state-update.md": "217c58934839eab905cd58feebac5d5307a024162ea51cf6a5c9c131ad1b2ab0",
        "engineer/targeted-fiction-revision.md": "f7e2d9e821ae9beb2f851fcd0be382636bb6511cd118760cede6287cfcf8030e",
        "reviewer/continuity-style-and-plot-review.md": "e4dff16951b478ba73d9afc69b97840b0288cbca4c2aa8e0c935cea21e9b2470",
    },
    "prose": {
        "reviewer/prose-review.md": "6571b39d6de6cc20f7c729c3cff391c07bdd9834e8e332b16d01864ad7b3d8db",
    },
    "modern_poetry": {
        "reviewer/modern-verse-review.md": "12af6d2c80843fb43ae9ab0f0c0a3b8312cbb325cced0427571f98ce23919757",
    },
    "classical_poetry": {
        "reviewer/prosody-and-conception-review.md": "f4774a1085b1751f07e44b7fa3612dca788f45b0f361a4fe11144dd523a315a0",
    },
    "literary_editor": {
        "reviewer/edit-review.md": "4026c87167388cf5c3a3846b162d0fa28208c869eb13b71cc84fbff8712e91c8",
    },
    "medical": {
        "engineer/target-disease-research.md": "22ed6c69a27379b7f494a85c71317b60cbe9d5176656dd2c3c6b7936c40a471f",
        "manager/medical-manager.md": "df9437e0726ff66368a5fa1c17507df14880dae0b5a7b4419ef6c50f94b9930d",
        "planner/medical-planning.md": "0d63fdbb93f55befffda8fd1c4f80acfa3f0603f6633b80a4df850d4b900ba66",
        "reviewer/medical-evidence-review.md": "25d2e461064ae9bea1d33a19c44a3977dcc28386ac3e42eaa794d9d3cc719274",
    },
    "materials": {
        "engineer/materials-atomistic-simulation.md": "f25b616dae6cb6b29bfeb40f1d511e2f360053afc4a7d10bf873ff284619c650",
        "engineer/materials-cad-cae-process-simulation.md": "9aa47450cfa2afe18ee5099c8cfc052c4111e14157e64e8fb6967a3e0e15b22e",
        "engineer/materials-data-literature-grounding.md": "354baed9452335c86256da4ee8d3cd2a7f8a029f827a8c41cd0fb05601296860",
        "engineer/materials-ecosystem-routing.md": "4050aa1d0ad457847b5d769d6b2fdae9321244c5d414d93cbbe122986500f4ba",
        "engineer/materials-experiment-loop.md": "217012b924203cc8cd9f99ac4b13b34a00ccefaf985bdcb2579ec1d16f1ebce2",
        "engineer/materials-research-execution.md": "25900094642969566df9ad2b6497facc0b51d40c4a9d87c4801bf0fa818ae0c7",
        "manager/materials-research-manager.md": "3b8133f2372ce1525ae68f8ee4457b081694a2495d1659773f77d128fae69463",
        "planner/materials-research-planning.md": "aac59ffaad7276929a3f766f44eab8e9996c1b5087685a8ee3b34c1a33d97ae8",
        "reviewer/materials-research-review.md": "ceb7c0caa00a89e641fe8f6b05a27d061b8b9c4c707e5b6b2d2f343ef608dbeb",
        "reviewer/materials-simulation-signoff.md": "21461c5898dbc44d57a3d9a5fcb5456fbbe547e5cbc9364bbe85ca2977f2eb09",
        "reviewer/materials-validation-review.md": "62230196f280f5538ea8b88207c83a025d9370492de42deea44e13eb59a07741",
    },
    "ale_last_exam": {
        "engineer/ale-last-exam-execution.md": "ab531ddd28e7d49dbb97ce9e7436cd365725d62db2318308bc8de02da2f4d53a",
        "reviewer/ale-last-exam-delivery-review.md": "04cf27944b1b3c13ab32ba6c45601a50fd3b53d69279aabad17a224c883ade73",
    },
}


def builtin_skill_source_path() -> Path:
    """Return the filesystem path for bundled skill markdown when available."""
    return Path(__file__).resolve().parents[1] / "builtin_skills"


def iter_builtin_skill_texts() -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for every bundled default skill."""
    root = resources.files(_BUILTIN_PACKAGE)
    yield from _iter_builtin_skill_resources(root)


def iter_common_builtin_skill_texts() -> Iterable[tuple[str, str]]:
    """Yield top-level common skills, excluding domain-pack subdirectories."""
    root = resources.files(_BUILTIN_PACKAGE)
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(("_", ".")) or not entry.name.endswith(".md"):
            continue
        yield entry.name, entry.read_text(encoding="utf-8")


def vertical_skill_source_path(vertical: str) -> Path:
    """Filesystem path of a vertical's own skills: ``verticals/<v>/skills``.

    The skill-layering convention: ``builtin_skills/`` holds cross-workflow
    skills, while each vertical ships workflow-specific skills under
    ``argus/verticals/<vertical>/skills/{engineer,reviewer}/``. This is the
    version-controlled read-only SOURCE for that vertical's skills.
    """
    if not vertical or "/" in vertical or "\\" in vertical or vertical.startswith("."):
        raise ValueError(f"invalid vertical name: {vertical!r}")
    return Path(__file__).resolve().parents[1] / "verticals" / vertical / "skills"


def domain_skill_source_path(domain: str) -> Path:
    """Filesystem path of a built-in domain's matchable Skills."""
    if not domain or "/" in domain or "\\" in domain or domain.startswith("."):
        raise ValueError(f"invalid domain name: {domain!r}")
    return Path(__file__).resolve().parents[1] / "domains" / domain / "skills"


def vertical_skill_parents(vertical: str) -> tuple[str, ...]:
    """Verticals whose skill trees are seeded before ``vertical``'s own, in order.

    A vertical declares its own parents: an entry-point plugin exposes
    ``VERTICAL_SKILL_PARENTS`` (validated by ``verticals._registry``); the
    built-in verticals declare none. A parent may itself be a plugin
    (``nanogpt_speedrun <- speedrun``) or built in (``kernelbench <-
    kernel_engineering``); ``_vertical_skill_roots`` resolves both the same way.
    """
    from ..verticals._registry import vertical_plugin

    plugin = vertical_plugin(vertical)
    return plugin.skill_parents if plugin is not None else ()


def _vertical_skill_roots(vertical: str) -> list[Traversable]:
    """Existing ``skills/`` roots for ``vertical``'s parents and then itself.

    A plugin vertical serves its tree from ``VerticalPlugin.skills_root``; a
    built-in one from ``verticals/<name>/skills`` inside this package.
    """
    from ..verticals._registry import vertical_plugin

    roots: list[Traversable] = []
    for source in (*vertical_skill_parents(vertical), vertical):
        plugin = vertical_plugin(source)
        root = (
            plugin.skills_root
            if plugin is not None and plugin.skills_root is not None
            else vertical_skill_source_path(source)
        )
        if root.is_dir():
            roots.append(root)
        elif source != vertical:
            log.warning(
                "vertical %r declares skill parent %r, which is neither an installed "
                "plugin with skills nor a built-in vertical with a skills directory",
                vertical, source,
            )
    return roots


def iter_vertical_skill_texts(vertical: str) -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for a vertical's own skills.

    Relative names are rooted at the vertical's ``skills/`` dir (e.g.
    ``reviewer/kernel-engineering-review.md``) so they match the
    ``<role>/<name>.md`` layout the vertical's checklist prose and
    ``role_banner`` reference verbatim, and overlay the same layout as the
    bundled builtins. Parents' skills come first; the vertical's own file of
    the same relative name wins nothing -- the first emitted name is kept.
    Fail-open: an unknown vertical or one with no ``skills/`` dir yields nothing.
    """
    emitted: set[str] = set()
    for root in _vertical_skill_roots(vertical):
        for filename, text in _iter_builtin_skill_resources(root):
            if filename in emitted:
                continue
            emitted.add(filename)
            yield filename, text


def iter_domain_skill_texts(domain: str) -> Iterable[tuple[str, str]]:
    """Yield ``(relative_filename, markdown)`` for one built-in domain."""
    root = domain_skill_source_path(domain)
    if root.is_dir():
        yield from _iter_builtin_skill_resources(root)


def iter_context_skill_texts(
    vertical: str,
    domain: str | None = None,
) -> Iterable[tuple[str, str]]:
    """Yield workflow Skills plus optional domain Skills, with domain overrides."""
    merged = dict(iter_vertical_skill_texts(vertical))
    if domain:
        merged.update(dict(iter_domain_skill_texts(domain)))
    yield from merged.items()


def _iter_reference_assets(
    root: Traversable,
    prefix: str = "",
    *,
    inside_references: bool = False,
) -> Iterable[tuple[str, str]]:
    """Yield supporting reference cards without making them matchable Skills."""
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(("_", ".")):
            continue
        relative_name = f"{prefix}{entry.name}"
        if entry.is_dir():
            yield from _iter_reference_assets(
                entry,
                f"{relative_name}/",
                inside_references=(
                    inside_references or entry.name == "references"
                ),
            )
        elif inside_references and entry.name.endswith(".md"):
            yield relative_name, entry.read_text(encoding="utf-8")


def iter_context_skill_assets(
    vertical: str,
    domain: str | None = None,
) -> Iterable[tuple[str, str]]:
    """Yield reference corpora consumed by context Skills.

    ``iter_context_skill_texts`` deliberately excludes ``references/`` because
    those cards are not independently matchable Skills. Excluding them from the
    seeder too left the owning Skill pointing at files that did not exist:
    run-01 had 43 of 94 research resources and none of the ideation cards
    (15 pattern cards and their overview since the 2026-10-01 prune).
    """
    merged: dict[str, str] = {}
    for root in _vertical_skill_roots(vertical):
        merged.update(dict(_iter_reference_assets(root)))
    if domain:
        root = domain_skill_source_path(domain)
        if root.is_dir():
            merged.update(dict(_iter_reference_assets(root)))
    yield from merged.items()


def _iter_builtin_skill_resources(
    root: Traversable,
    prefix: str = "",
) -> Iterable[tuple[str, str]]:
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(("_", ".")):
            continue
        relative_name = f"{prefix}{entry.name}"
        if entry.is_dir():
            # Reference corpora are package assets consumed by their owning
            # skill, not independently matchable skills.
            if entry.name == "references":
                continue
            yield from _iter_builtin_skill_resources(entry, f"{relative_name}/")
        elif entry.name.endswith(".md"):
            yield relative_name, entry.read_text(encoding="utf-8")
        elif _is_bundled_script(prefix, entry.name):
            # Scripts that ship alongside a skill (e.g.
            # engineer/figure_spec_scripts/figure_renderer.py) live in
            # ``*_scripts/`` subdirs and are seeded verbatim so the
            # skill can invoke them in the project workspace.
            yield relative_name, entry.read_text(encoding="utf-8")


_BUNDLED_SCRIPT_EXTENSIONS = (".py", ".json", ".sh")


def _is_bundled_script(prefix: str, filename: str) -> bool:
    """A file is a bundled-script asset iff it lives under a
    ``*_scripts/`` directory and has a known script extension."""
    if not any(filename.endswith(ext) for ext in _BUNDLED_SCRIPT_EXTENSIONS):
        return False
    # ``prefix`` ends with "/" by construction; split into segments.
    segments = [s for s in prefix.split("/") if s]
    return any(seg.endswith("_scripts") for seg in segments)


def _moved_global_skill_names() -> set[str]:
    moved: set[str] = set()
    verticals_root = Path(__file__).resolve().parents[1] / "verticals"
    for marker in verticals_root.glob(f"*/skills/{_MOVED_SKILL_MARKER}"):
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        values = payload.get("paths", ()) if isinstance(payload, dict) else payload
        if isinstance(values, list):
            moved.update(
                str(value)
                for value in values
                if isinstance(value, str) and value.strip()
            )
    return moved


def _seed_content_digests(body: bytes) -> set[str]:
    """Recognize factory copies across LF/CRLF conversion, preserving other bytes.

    Older Windows seeds were written as CRLF while their manifests hashed the
    LF source text. Keep raw hashes compatible with existing manifests too.
    Whitespace, a missing final newline, and all content changes still count
    as operator edits; only line-ending representation is interchangeable.
    """
    lf_body = body.replace(b"\r\n", b"\n")
    return {
        hashlib.sha256(candidate).hexdigest()
        for candidate in (body, lf_body, lf_body.replace(b"\n", b"\r\n"))
    }


def retire_uninstalled_vertical_seeds(skills_dir: Path) -> list[str]:
    """Remove factory copies of seeds whose vertical left the package and is not installed.

    Only a byte-identical factory copy (``_MOVED_VERTICAL_SEED_HASHES``) is
    removed; an operator's edited copy is left exactly where it is -- not
    archived -- because the vertical may be installed later and the edit is
    then its rightful project-layer skill. A vertical that *is* installed is
    skipped entirely; its seeds are the plugin's to manage.
    """
    from .vertical_select import available_verticals

    root = Path(skills_dir)
    present = set(available_verticals())
    removed: list[str] = []
    for vertical, seeds in _MOVED_VERTICAL_SEED_HASHES.items():
        if vertical in present:
            continue
        for relative_name, expected_digest in seeds.items():
            if relative_name in removed:
                continue
            path = root / relative_name
            try:
                body = path.read_bytes()
            except (FileNotFoundError, IsADirectoryError, OSError):
                continue
            if expected_digest not in _seed_content_digests(body):
                continue
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(relative_name)
    return sorted(removed)


def retire_orphaned_builtin_seeds(skills_dir: Path, *, include_moved: bool = True) -> list[str]:
    """Remove retired seeds from matching, archiving any operator-edited copy.

    Also removes unmodified seeds of community verticals that are not
    installed here (``retire_uninstalled_vertical_seeds``); those are never
    archived.
    """
    skills_dir = Path(skills_dir)
    state = _seed_state(skills_dir)
    retired = dict(_RETIRED_BUILTIN_SEED_HASHES)
    retired.update({
        relative: state.get(relative)
        or _LEGACY_BUILTIN_SEED_HASHES.get(relative)
        or ""
        for relative in (_moved_global_skill_names() if include_moved else ())
    })
    removed: list[str] = []
    for relative_name, expected_digest in sorted(
        retired.items()
    ):
        path = skills_dir / relative_name
        try:
            body = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, OSError):
            continue
        if expected_digest and expected_digest in _seed_content_digests(body):
            try:
                path.unlink()
            except OSError:
                continue
        else:
            archive = (
                skills_dir
                / "_retired_builtin_skills"
                / f"{relative_name}.retired"
            )
            archive.parent.mkdir(parents=True, exist_ok=True)
            if archive.exists():
                try:
                    if archive.read_bytes() == body:
                        path.unlink()
                    else:
                        digest = hashlib.sha256(body).hexdigest()[:12]
                        path.replace(
                            archive.with_name(f"{archive.name}.{digest}")
                        )
                except OSError:
                    continue
            else:
                try:
                    path.replace(archive)
                except OSError:
                    continue
        removed.append(relative_name)
    removed.extend(retire_uninstalled_vertical_seeds(skills_dir))
    return removed

def _seed_state(skills_dir: Path) -> dict[str, str]:
    try:
        payload = json.loads(
            (skills_dir / _BUILTIN_SEED_STATE).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(name): str(digest)
        for name, digest in payload.items()
        if isinstance(name, str) and isinstance(digest, str)
    }


def _seed_texts(
    skills_dir: Path,
    texts: Iterable[tuple[str, str]],
    *,
    overwrite: bool,
) -> dict[str, bool]:
    state = _seed_state(skills_dir)
    previous_state = dict(state)
    created: dict[str, bool] = {}
    for filename, text in texts:
        text = text.replace("\r\n", "\n")
        if filename.endswith(".md"):
            _validate_builtin(filename, text)
        dest = skills_dir / filename
        source_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            installed_body = dest.read_bytes()
            installed_digest = hashlib.sha256(installed_body).hexdigest()
            installed_digests = _seed_content_digests(installed_body)
        except (FileNotFoundError, IsADirectoryError):
            installed_digest = ""
            installed_digests = set()
        except OSError:
            created[filename] = False
            continue
        prior_digest = state.get(filename, "")
        factory_owned = (
            not installed_digest
            or source_digest in installed_digests
            or prior_digest in installed_digests
            or _LEGACY_BUILTIN_SEED_HASHES.get(filename) in installed_digests
        )
        if not overwrite and not factory_owned:
            created[filename] = False
            continue
        changed = installed_digest != source_digest
        if changed:
            dest.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(dest, text)
        state[filename] = source_digest
        created[filename] = changed
    if state != previous_state:
        _atomic_write_text(
            skills_dir / _BUILTIN_SEED_STATE,
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )
    return created


def seed_builtin_skills(skills_dir: Path, *, overwrite: bool = False) -> dict[str, bool]:
    """Seed bundled skills into ``skills_dir``.

    Existing files are preserved by default. The return value maps each
    bundled filename to ``True`` when it was created/replaced and ``False``
    when an existing user file was left untouched.
    """
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    retire_orphaned_builtin_seeds(skills_dir)
    return _seed_texts(
        skills_dir,
        iter_builtin_skill_texts(),
        overwrite=overwrite,
    )


def seed_builtin_skills_for_vertical(
    skills_dir: Path,
    vertical: str,
    *,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Compatibility wrapper for a workflow without a domain overlay."""
    return seed_builtin_skills_for_context(
        skills_dir,
        vertical,
        overwrite=overwrite,
    )


def seed_builtin_skills_for_context(
    skills_dir: Path,
    vertical: str,
    *,
    domain: str | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Seed COMMON builtins + a vertical's own skills into ``skills_dir``.

    Used to populate a mission's project workspace (``argus_builtin_skills/``) or
    the runtime shared-scope layer so the agent sees common Skills plus the active
    workflow and optional domain Skills. Context-specific real bodies
    OVERWRITE any same-path builtin stub (a moved domain skill leaves a pointer
    stub under ``builtin_skills/``; here the real body wins), so the workspace
    never carries the pointer.

    Note: this uses the FULL bundled set (``iter_builtin_skill_texts``), not
    ``iter_common_builtin_skill_texts`` — the latter skips the ``engineer/`` and
    ``reviewer/`` subdirectories, which is exactly where the cross-vertical
    skills live. Files the vertical will overwrite are skipped on the builtin
    pass so a pointer stub is never written into the workspace at all.

    Returns a map of relative filename → created/replaced (True) or skipped
    (False, an existing file left untouched because ``overwrite`` is False).
    """
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    retire_orphaned_builtin_seeds(skills_dir, include_moved=False)
    # Workflow/domain Skills (real bodies) always win over a builtin
    # stub of the same relative path.
    vertical_texts = dict(iter_context_skill_texts(vertical, domain))

    # 1. Common/bundled builtins, skipping any path the vertical will overwrite
    #    (so a pointer stub is never written into the workspace).
    created = _seed_texts(
        skills_dir,
        (
            (filename, text)
            for filename, text in iter_builtin_skill_texts()
            if filename not in vertical_texts
        ),
        overwrite=overwrite,
    )

    # 2. Context-specific real bodies win when explicitly requested, newly
    # seeded, or still factory-owned; operator edits remain intact.
    created.update(
        _seed_texts(
            skills_dir,
            vertical_texts.items(),
            overwrite=overwrite,
        )
    )
    created.update(
        _seed_texts(
            skills_dir,
            iter_context_skill_assets(vertical, domain),
            overwrite=overwrite,
        )
    )

    return created


def seed_vertical_skills(
    skills_dir: Path,
    vertical: str,
    *,
    overwrite: bool = False,
    overwrite_unidentified: bool = False,
) -> dict[str, bool]:
    """Compatibility wrapper for a vertical-only runtime layer."""
    return seed_context_skills(
        skills_dir,
        vertical,
        overwrite=overwrite,
        overwrite_unidentified=overwrite_unidentified,
    )


def seed_context_skills(
    skills_dir: Path,
    vertical: str,
    *,
    domain: str | None = None,
    overwrite: bool = False,
    overwrite_unidentified: bool = False,
) -> dict[str, bool]:
    """Seed only the active workflow/domain context into one runtime layer."""
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    # Moved global paths are valid here; never retire learned vertical copies.
    retire_orphaned_builtin_seeds(skills_dir, include_moved=False)
    _ = overwrite_unidentified  # Retained caller compatibility; edits stay owned.
    created = _seed_texts(
        skills_dir, iter_context_skill_texts(vertical, domain), overwrite=overwrite,
    )
    created.update(_seed_texts(
        skills_dir, iter_context_skill_assets(vertical, domain), overwrite=overwrite,
    ))
    return created



def remove_unmodified_vertical_skill_seeds(
    skills_dir: Path,
    vertical: str,
) -> list[str]:
    """Remove legacy project-layer factory copies without touching learned edits."""
    root = Path(skills_dir)
    removed: list[str] = []
    for filename, source_text in iter_vertical_skill_texts(vertical):
        path = root / filename
        try:
            if path.is_file() and path.read_text(encoding="utf-8") == source_text:
                path.unlink()
                removed.append(filename)
        except OSError:
            continue
    return removed


def remove_unmodified_inactive_context_skill_seeds(
    skills_dir: Path,
    active_vertical: str | None,
    *,
    active_domain: str | None = None,
) -> list[str]:
    """Remove unedited factory copies outside the active workflow/domain context."""
    from ..domains import BUILTIN_DOMAINS
    from .vertical_select import available_verticals

    root = Path(skills_dir)
    active_filenames = (
        {
            filename
            for filename, _text in iter_context_skill_texts(
                active_vertical,
                active_domain,
            )
        }
        if active_vertical
        else set()
    )
    removed: set[str] = set()
    for vertical in available_verticals():
        if vertical == active_vertical:
            continue
        for filename, source_text in iter_vertical_skill_texts(vertical):
            if filename in active_filenames or filename in removed:
                continue
            path = root / filename
            try:
                if path.is_file() and path.read_text(encoding="utf-8") == source_text:
                    path.unlink()
                    removed.add(filename)
            except OSError:
                continue
    for domain in BUILTIN_DOMAINS:
        if domain == active_domain:
            continue
        for filename, source_text in iter_domain_skill_texts(domain):
            if filename in active_filenames or filename in removed:
                continue
            path = root / filename
            try:
                if path.is_file() and path.read_text(encoding="utf-8") == source_text:
                    path.unlink()
                    removed.add(filename)
            except OSError:
                continue
    return sorted(removed)


def _validate_builtin(filename: str, text: str) -> None:
    # Source-controlled bundled documents are interpreted by Agents. Runtime
    # validation only rejects an empty file and does not parse frontmatter.
    if not text.strip():
        raise ValueError(f"bundled Skill is empty: {filename}")
    return True


def _atomic_write_text(path: Path, text: str) -> None:
    text = text.replace("\r\n", "\n")
    tmp = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}.{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                try:
                    if path.read_bytes().replace(b"\r\n", b"\n") == text.encode("utf-8"):
                        return
                except OSError:
                    pass
                if attempt == 7:
                    raise
                time.sleep(min(0.02 * (2**attempt), 0.25))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
