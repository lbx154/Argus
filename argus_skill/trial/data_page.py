"""Private, task-centred workbench for genuine collaboration and training records."""

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Argus · 数据工作台</title>
<script src="/admin/data/app.js" defer></script><style>
:root{color-scheme:light;font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#262934;background:#f7f8fa;--ink:#262934;--muted:#858995;--line:#e9eaf0;--accent:#635bca;--accent-soft:#f0eefa;--green:#298570;--green-soft:#eaf6f0;--amber:#ad7c35;--amber-soft:#fbf3e4}
*{box-sizing:border-box}body{margin:0}button,input,select{font:inherit}a{color:inherit;text-decoration:none}button{cursor:pointer}button:disabled{opacity:.42;cursor:default}button,input,select{border:1px solid var(--line);border-radius:8px;background:white;color:var(--ink);padding:8px 11px}button:hover:not(:disabled){border-color:#c8c4e9;background:#faf9ff}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #a7a0ed;outline-offset:3px}input[type=checkbox]{accent-color:var(--accent);width:15px;height:15px;margin:2px 7px 0 0;vertical-align:top}h1,h2,h3,h4,p{margin:0}h1{font-size:25px;letter-spacing:-.7px;font-weight:650}h2{font-size:19px;letter-spacing:-.35px;font-weight:650}h3{font-size:14px;font-weight:650}small,.muted{color:var(--muted)}small{font-size:12px}.icon{width:19px;height:19px;display:inline-flex;align-items:center;justify-content:center;flex:none}.icon svg{width:100%;height:100%;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}.sidebar{position:fixed;inset:0 auto 0 0;width:205px;background:#fbfbfd;border-right:1px solid var(--line);padding:29px 18px;display:flex;flex-direction:column;z-index:10}.brand{display:flex;align-items:center;gap:10px;font-size:20px;letter-spacing:2px;font-weight:720;margin:0 10px 44px}.brand-mark{display:grid;place-items:center;background:var(--accent);color:white;width:30px;height:32px;border-radius:9px;font-size:19px;font-weight:600;letter-spacing:-3px;padding-right:3px}.nav-caption{font-size:10px;color:#a3a5af;letter-spacing:1.4px;padding:0 12px;margin:0 0 12px}.nav-link{border:0;background:none;display:flex;align-items:center;text-align:left;gap:10px;padding:11px 12px;width:100%;margin:3px 0;font-size:13px;color:#7b7f8c;border-radius:8px}.nav-link.active{background:var(--accent-soft);color:var(--accent);font-weight:620}.nav-link:hover{background:#f0f0f5}.sidebar-bottom{margin-top:auto}.operator{border-top:1px solid var(--line);margin:20px 10px 0;padding-top:20px;display:flex;align-items:center;gap:10px;font-size:12px}.avatar{width:32px;height:32px;background:#efedf6;color:var(--accent);display:grid;place-items:center;border-radius:50%;font-size:11px;font-weight:650}.operator small{display:block;font-size:10px}.shell{margin-left:205px}.topbar{height:72px;border-bottom:1px solid var(--line);padding:0 34px;display:flex;align-items:center;justify-content:space-between;background:#fff;font-size:12px;color:var(--muted)}.breadcrumb{display:flex;align-items:center;gap:13px}.breadcrumb strong{color:#454955;font-weight:500}.top-actions{display:flex;align-items:center;gap:12px}.connection{display:flex;align-items:center;gap:7px;font-size:11px}.connection[data-state=error] .status-dot{background:#c48b62}.connection[data-state=loading] .status-dot{background:#b6b1c7}.status-dot{width:6px;height:6px;border-radius:50%;background:#45a183}.quiet-button{background:transparent;border:0;color:var(--muted);font-size:12px;padding:6px}.button-icon{display:inline-flex;align-items:center;justify-content:center;gap:7px}.primary{background:var(--accent);color:white;border-color:var(--accent);font-weight:550}.primary:hover:not(:disabled){background:#544cb7;border-color:#544cb7}.main{max-width:1660px;margin:auto;padding:32px 34px 50px}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:25px}.page-heading p{color:var(--muted);font-size:12px;margin-top:5px}.page-heading>div:first-child{flex:1;min-width:0}.page-heading .actions{display:flex;gap:8px;flex:none}.page-heading .actions button{white-space:nowrap}.metrics{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);background:white;border-radius:12px;margin-bottom:28px;padding:23px 0}.metric{padding:0 24px;border-right:1px solid var(--line)}.metric:last-child{border:0}.metric-label{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:7px}.metric strong{font-size:29px;font-weight:630;line-height:1.3;display:block;letter-spacing:-.8px;margin:8px 0 3px}.metric small{font-size:10px}.metric:last-child strong{color:var(--green)}.library-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:13px}.library-heading h2{font-size:15px}.scope-label{font-size:11px;color:#9296a1}.workspace{display:grid;grid-template-columns:300px minmax(0,1fr);border:1px solid var(--line);border-radius:13px;background:#fff;min-height:700px;overflow:hidden}#projects{max-height:800px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#dedde8 transparent}.task-library{border-right:1px solid var(--line);background:#fdfdfe;display:flex;flex-direction:column;min-width:0}.search-wrap{padding:18px 16px 12px}.search-field{position:relative;display:flex;align-items:center;gap:8px;background:#f6f7f9;border:1px solid #eeeef3;border-radius:7px;padding-left:10px;color:#9599a4}.search-field input{background:none;border:0;border-radius:0;width:100%;outline:none;font-size:12px;padding:8px 4px}.search-field .icon{width:15px;height:15px}.list-toolbar{display:flex;align-items:center;justify-content:space-between;padding:0 18px 12px;font-size:11px;color:var(--muted)}.list-toolbar select{font-size:11px;border:0;background:none;color:#737786;padding:2px 0}.project{border-top:1px solid #f0f1f5;position:relative}.project:first-child{border-top:0}.project.active{background:#f2f0fb}.project.active:before{content:"";position:absolute;left:0;top:15px;bottom:15px;width:3px;background:var(--accent);border-radius:0 3px 3px 0}.project-open{border:0;border-radius:0;background:none;text-align:left;width:100%;padding:17px 18px}.project-open:hover:not(:disabled){background:#f7f6fc}.project.active .project-open:hover{background:#f2f0fb}.project-title{font-size:13px;font-weight:580;line-height:1.55;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;margin-bottom:9px}.project-meta{display:flex;align-items:center;justify-content:space-between;gap:7px}.project-meta small{font-size:10px}.project-updated{font-size:9px;color:#a5a8b3;margin-top:7px}.tag,.badge{display:inline-flex;align-items:center;gap:5px;font-size:10px;line-height:1.4;padding:4px 7px;background:#f1f2f5;color:#8a8d97;border-radius:5px;white-space:nowrap;font-weight:500}.tag.ready,.badge.ready{color:var(--green);background:var(--green-soft)}.tag.purple,.badge.purple{background:var(--accent-soft);color:var(--accent)}.tag.amber,.badge.amber{background:var(--amber-soft);color:var(--amber)}.project-source{display:none;margin:0 18px 10px;font-size:10px;color:#9497a2}.project-source summary{font-size:10px}.project-source span{display:block;word-break:break-all;padding:5px 0}.paging{display:flex;align-items:center;justify-content:space-between;gap:7px;padding:16px;margin-top:auto;border-top:1px solid var(--line)}.paging button{font-size:10px;padding:4px 6px;border:0;background:none;color:#8c8f9a}.paging span{font-size:10px;color:#979aa5}.task-detail{min-width:0}.detail-heading{padding:26px 28px 19px}.detail-topline{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:13px}.detail-kicker{font-size:10px;color:#9699a4;letter-spacing:.4px}.detail-heading h2{font-size:21px;font-weight:640;line-height:1.5;margin-bottom:9px}.task-meta{display:flex;align-items:center;gap:13px;flex-wrap:wrap;font-size:11px;color:#8b8f9b}.task-meta .separator{color:#d4d6dd}.goal-preview{font-size:12px;line-height:1.8;color:#808591;margin-top:13px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.task-goal{margin-top:12px;font-size:11px;color:#8b8f9b}.tabs{display:flex;gap:26px;border-bottom:1px solid var(--line);padding:0 28px}.tab{border:0;background:none;border-radius:0;color:#9497a2;font-size:12px;padding:12px 0 14px;position:relative}.tab.active{color:var(--accent);font-weight:620}.tab.active:after{position:absolute;content:"";bottom:-1px;left:0;right:0;background:var(--accent);height:2px}.tab-count{font-size:10px;background:#f0eef9;border-radius:4px;padding:1px 5px;margin-left:5px}.tab-pane{padding:25px 28px 30px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:15px;margin-bottom:16px}.section-head h3{font-size:13px}.section-head small{font-size:10px}.subtle-copy{color:#9598a3;font-size:11px;line-height:1.8}.flow{display:flex;gap:10px;align-items:stretch;overflow-x:auto;padding:2px 1px 10px;margin-bottom:5px}.role-card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 13px;min-width:145px;flex:1;position:relative;text-align:left;white-space:normal}.role-card.active{border-color:#b7b0e5;background:#fbfaff;box-shadow:0 0 0 2px #f5f2fd}.role-card-top{display:flex;gap:8px;align-items:center;margin-bottom:11px}.role-symbol{width:29px;height:29px;display:grid;place-items:center;border-radius:8px;background:#f0eefb;color:#776fc3;font-size:13px;font-weight:550}.role-card:nth-child(3n+2) .role-symbol{background:#edf3f9;color:#6285aa}.role-card:nth-child(3n) .role-symbol{background:#ecf5ef;color:#6f9680}.role-name{font-size:12px;font-weight:590}.role-caption{font-size:9px;color:#9a9da8;margin-top:1px}.role-card .badge{margin-top:8px}.role-card .tag{font-size:9px;padding:3px 5px;margin-bottom:8px}.role-card-meta{font-size:10px;color:#9195a0}.role-card-meta strong{font-weight:600;color:#606571}.flow-note{font-size:10px;color:#9b9eaa;margin-top:8px;line-height:1.8}.handoffs{display:flex;flex-wrap:wrap;gap:7px;margin:10px 0}.handoff{font-size:10px;color:#8f8aaa;padding:5px 8px;background:#f8f7fc;border:1px solid #eeebf6;border-radius:5px}.callout{padding:13px 15px;border:1px solid #edeaf6;border-radius:8px;background:#faf9fd;display:flex;align-items:flex-start;gap:10px;margin-top:18px}.callout .icon{color:#9d97c5;width:16px;height:16px;margin-top:2px}.callout strong{display:block;font-size:11px;color:#70678f;font-weight:550;margin-bottom:2px}.callout p{font-size:10px;color:#9690a7;line-height:1.7}.collaboration-steps{margin-top:26px;border-top:1px solid var(--line);padding-top:23px}.role-run-heading{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:16px}.role-run-heading h3{font-size:13px}.run-picker{font-size:10px;padding:4px 6px;max-width:220px}.empty-state{padding:45px 20px;text-align:center;color:#9599a5;font-size:12px;line-height:1.9}.empty-state strong{display:block;color:#767b89;font-weight:500;margin-bottom:6px}.empty-state .icon{width:34px;height:34px;color:#c9c5df;margin:0 auto 10px}.more-steps{margin:0 0 12px}.more-steps>summary{font-size:11px;color:var(--accent);cursor:pointer;padding:8px 0 13px}.retained-notice{font-size:11px;line-height:1.9;color:#978367;background:#fcf8ef;border:1px solid #f0e8d8;border-radius:8px;padding:11px 13px;margin:0 0 15px}.activity-timeline{margin:15px 0 0;padding:12px 14px;background:#fafafd;border:1px solid #eeedf4;border-radius:8px}.activity-timeline:empty{display:none}.activity-row{border:0;background:none;width:100%;padding:6px 0;display:flex;align-items:center;gap:10px;text-align:left;font-size:10px}.activity-row:hover:not(:disabled){background:#f2f0f8;border:0}.activity-time{color:#9a9cab;font-variant-numeric:tabular-nums}.activity-marker{width:5px;height:5px;border-radius:50%;background:#b7b1d7;flex:none}.activity-row strong{font-weight:550;color:#7c7598}.activity-summary{color:#9694a4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.timeline{display:flex;flex-direction:column;gap:0}.tool-step{position:relative;border:1px solid #e9eaf0;border-radius:8px;margin:0 0 10px;background:white;overflow:hidden}.tool-step summary{display:flex;align-items:center;gap:10px;cursor:pointer;padding:12px 13px;list-style:none}.tool-step summary::-webkit-details-marker{display:none}.step-number{font-size:9px;color:#9397a3;background:#f4f4f7;border:1px solid #eeeeF3;border-radius:5px;width:22px;height:22px;display:grid;place-items:center;flex:none}.step-heading{flex:1;min-width:0}.step-heading strong{display:block;font-size:11px;font-weight:550;color:#555b69;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}.step-heading small{font-size:10px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;margin-top:1px}.step-check{color:#6ba590;font-size:12px}.step-arrow{color:#b0b3bd;font-size:13px;margin-left:4px}.tool-step[open] .step-arrow{transform:rotate(90deg)}.tool-body{border-top:1px solid #eeeef4;padding:14px 15px;background:#fcfcfe}.tool-body h4{font-size:10px;font-weight:550;color:#9295a3;margin:0 0 6px}.tool-body pre{margin:0 0 14px}.tool-body pre:last-child{margin-bottom:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.75 ui-monospace,SFMono-Regular,Consolas,monospace;color:#74798a;max-height:340px;overflow:auto}.message{margin-bottom:12px}.message-summary{border:1px solid var(--line);border-radius:8px;padding:12px 14px;background:#fcfcfe;margin-bottom:12px}.message-summary summary{font-size:11px;color:#767b89;cursor:pointer}.message-summary pre{margin:10px 0 0;font-family:inherit;font-size:11px;line-height:1.9}.result-message{border:1px solid #e4eee8;background:#f8fcf9}.result-message summary{color:#5d8270}.role{font-size:10px;color:#7e789e;margin-bottom:5px}.technical-details{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}.technical-details>summary{font-size:11px;color:#989caa;cursor:pointer}.technical-details pre{margin-top:11px}.samples-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:15px}.samples-toolbar select,.samples-toolbar input{font-size:11px;padding:6px 8px;max-width:160px}.samples-toolbar input{max-width:180px}.samples-toolbar .search-field{flex:1;max-width:220px}.sample-list{display:flex;gap:8px;overflow-x:auto;margin-bottom:20px}.sample{padding:11px 13px;min-width:170px;max-width:240px;flex:0 0 auto;text-align:left;display:block;border-radius:8px;border:1px solid var(--line);background:white}.sample.active{border-color:#bdb7e6;background:#fbfaff}.sample strong{font-size:11px;font-weight:560;display:block;margin:0 0 5px}.sample small{font-size:10px;display:block}.sample .badge{font-size:9px;padding:2px 5px;margin-top:8px}.sample-inspector h3{font-size:14px;margin-bottom:8px}.sample-badges{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}.sample-approval{border:1px solid #eae8f3;background:#faf9fd;padding:11px 12px;border-radius:8px;display:flex;align-items:flex-start;font-size:11px;color:#827b97;margin:15px 0 18px}.sample-state{font-size:11px;color:#9498a4;line-height:1.8}.sample-id{font-size:10px;word-break:break-all;color:#a0a3ad}.task-detail.empty .detail-heading{display:none}.divider{border-top:1px solid var(--line);margin:22px 0}.footnote{font-size:10px;color:#a1a4ae;margin-top:14px}.status-line{font-size:10px;color:#a1a5b0;min-height:16px;display:flex;justify-content:space-between;gap:15px;margin-top:12px}#error{color:#b56464;font-size:12px;margin-top:10px}#error:empty{display:none}.drawer-backdrop{position:fixed;inset:0;background:rgba(31,32,47,.2);backdrop-filter:blur(2px);z-index:30}.drawer{position:fixed;right:0;top:0;bottom:0;width:min(580px,100%);z-index:31;background:white;box-shadow:-10px 0 60px #23203f12;overflow:auto;padding:28px 30px}.drawer-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}.drawer-header h2{font-size:18px}.drawer-close{border:0;background:#f4f4f8;border-radius:50%;width:28px;height:28px;padding:0;color:#9295a1;font-size:18px}.drawer-section{margin-bottom:27px}.drawer-section h3{font-size:13px;margin-bottom:11px}.drawer-section p{font-size:11px;color:#989ca9;margin-bottom:12px;line-height:1.8}.drawer-section label{font-size:11px;color:#767b89;display:block;line-height:1.8;margin:11px 0}.drawer-section label input:not([type=checkbox]),.drawer-section label select{display:block;width:100%;margin-top:6px;font-size:12px}.drawer-section details{border-top:1px solid var(--line);margin-top:13px;padding-top:13px}.drawer-section summary{font-size:11px;color:#8d92a0;cursor:pointer}.drawer-section pre{margin:10px 0}.export-projects{max-height:270px;overflow:auto;border:1px solid var(--line);padding:4px 12px;border-radius:8px;margin-bottom:15px}.export-project-label{display:flex!important;gap:4px;align-items:flex-start}.export-project-label small{display:block;font-size:10px}.export-project-label span{flex:1}.drawer .primary{width:100%;padding:11px}#export-status{font-size:11px;color:#847b9e;margin-top:12px}.table{overflow:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;border-bottom:1px solid var(--line);padding:10px 6px;font-size:10px;vertical-align:top;word-break:break-all}th{font-weight:500;color:#8e93a0}td{color:#979ca7}.filter-row{display:flex;gap:10px}.filter-row label{flex:1;min-width:0}.exclusion-row{display:flex;justify-content:space-between;gap:16px;font-size:11px;padding:8px 0;color:#8e94a1;border-bottom:1px solid #f0f1f5}.reason-label{overflow-wrap:anywhere}[hidden]{display:none!important}
@media(min-width:1600px){.workspace{grid-template-columns:330px minmax(0,1fr)}.role-card{padding:17px}.role-name{font-size:13px}.main{padding-top:38px}.detail-heading{padding:30px 34px 23px}.tabs{padding:0 34px}.tab-pane{padding:28px 34px}}
@media(max-width:1180px){.sidebar{width:170px;padding:26px 12px}.shell{margin-left:170px}.main{padding:25px 22px}.topbar{padding:0 22px}.workspace{grid-template-columns:255px minmax(0,1fr)}.detail-heading{padding:23px 21px 17px}.tabs{padding:0 21px}.tab-pane{padding:22px 21px}.role-card{min-width:140px}.metric{padding:0 19px}}
@media(max-width:900px){.sidebar{width:67px;padding:24px 10px}.brand{margin:0 auto 36px}.brand-text,.nav-caption,.nav-link .nav-text,.operator div:not(.avatar){display:none}.nav-link{justify-content:center;padding:11px}.operator{margin:20px 5px 0}.shell{margin-left:67px}.workspace{grid-template-columns:230px minmax(0,1fr)}.main{padding:25px 18px}.topbar{padding:0 18px}.page-heading h1{font-size:22px}.metric{padding:0 14px}.metric strong{font-size:25px}.metric small{font-size:9px}.detail-heading h2{font-size:18px}.samples-toolbar{flex-wrap:wrap}}
@media(max-width:700px){.sidebar{display:none}.shell{margin-left:0}.topbar{height:55px;padding:0 18px}.connection{display:none}.main{padding:22px 14px 35px}.page-heading{align-items:flex-start;margin-bottom:20px}.page-heading p{font-size:11px;max-width:170px}.page-heading .actions button{font-size:11px;padding:7px 9px}.page-heading .actions .optional-label{display:none}.metrics{padding:16px 0;margin-bottom:21px;grid-template-columns:repeat(2,1fr);gap:18px 0}.metric{padding:0 18px}.metric:nth-child(2){border:0}.metric strong{font-size:24px;margin:5px 0 1px}.metric-label{font-size:10px}.workspace{display:block;min-height:0}#projects{max-height:800px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#dedde8 transparent}.task-library{border-right:0;border-bottom:1px solid var(--line)}.search-wrap{padding:14px 14px 9px}.list-toolbar{padding:0 15px 10px}#projects{display:flex;overflow-x:auto;overflow-y:hidden;max-height:none;border-top:1px solid var(--line);padding:0 6px;gap:0}.project{border:0;border-right:1px solid #efeff4;min-width:215px;max-width:215px}.project.active:before{top:auto;bottom:0;height:2px;left:14px;right:14px;width:auto}.project-open{padding:14px 12px}.project-source{display:none}.project-title{font-size:12px;min-height:38px}.paging{padding:8px 14px}.detail-heading{padding:22px 18px 16px}.tabs{padding:0 18px;gap:24px}.tab-pane{padding:22px 18px}.role-card{min-width:144px}.flow-note:after{content:" · 左右滑动可查看全部角色"}.section-head small{max-width:140px;text-align:right}.drawer{padding:24px 20px}.status-line{font-size:9px}.top-actions .quiet-button{font-size:10px}}
</style></head><body>
<aside class="sidebar"><a href="/admin" class="brand" aria-label="Argus 运营后台"><span class="brand-mark">A</span><span class="brand-text">ARGUS</span></a>
<div class="nav-caption">工作空间</div><a class="nav-link" href="/admin"><span class="icon"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg></span><span class="nav-text">运营概览</span></a>
<a class="nav-link active" href="/admin/data" aria-current="page"><span class="icon"><svg viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></svg></span><span class="nav-text">数据工作台</span></a>
<button class="nav-link" id="nav-audit"><span class="icon"><svg viewBox="0 0 24 24"><path d="M8 3h8l4 4v14H4V3h4zm8 0v5h4M8 12h8m-8 4h6"/></svg></span><span class="nav-text">导出与审计</span></button>
<div class="sidebar-bottom"><a class="nav-link" href="/invite"><span class="icon"><svg viewBox="0 0 24 24"><path d="M14 4h6v6m0-6L10 14m-3-8H4v14h14v-3"/></svg></span><span class="nav-text">进入用户端</span></a><div class="operator"><div class="avatar">AD</div><div>团队管理员<small id="session-label">私有工作空间</small></div></div></div></aside>
<div class="shell"><header class="topbar"><div class="breadcrumb"><span>工作空间</span><span>/</span><strong>数据工作台</strong></div><div class="top-actions"><span class="connection" id="connection"><span class="status-dot"></span><span id="connection-label">连接中</span></span><button class="quiet-button" id="open-diagnostics">采集诊断</button></div></header>
<main class="main"><div class="page-heading"><div><h1>数据工作台</h1><p>从一次任务，看见 Agent 协作与可用的训练样本。</p></div><div class="actions"><button class="button-icon" id="reload"><span class="icon"><svg viewBox="0 0 24 24"><path d="M20 7v5h-5M4 17v-5h5M6.2 7a7 7 0 0 1 11.6-1L20 9M4 15l2.2 3A7 7 0 0 0 18 17"/></svg></span><span class="optional-label">刷新</span></button><button class="primary button-icon" id="open-export"><span class="icon"><svg viewBox="0 0 24 24"><path d="M12 3v12m-4-4 4 4 4-4M5 16v5h14v-5"/></svg></span>导出数据</button></div></div>
<div id="metrics" class="metrics" aria-label="本页数据概览"></div><div class="library-heading"><h2>任务库</h2><span id="overview-scope" class="scope-label">按真实任务记录归集</span></div>
<p id="overview-limits" class="subtle-copy" style="margin:-3px 0 13px" hidden></p><div class="workspace"><aside class="task-library"><div class="search-wrap"><label class="search-field"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/></svg></span><input id="project-query" placeholder="搜索本页任务" aria-label="搜索本页任务" maxlength="160"></label></div><div class="list-toolbar"><span id="project-count">任务记录</span><select id="task-view" aria-label="任务显示范围"><option value="all">全部任务</option><option value="ready">已有合格样本</option><option value="pending">待验证样本</option><option value="activity">项目活动</option></select></div><div id="projects"></div><div class="paging"><button id="prev" disabled>← 上一页</button><span id="page"></span><button id="next" disabled>下一页 →</button></div></aside>
<section class="task-detail" id="task-detail"><div class="detail-heading"><div class="detail-topline"><span class="detail-kicker" id="task-category">任务详情</span><span id="task-outcome"></span></div><h2 id="task-title">正在读取任务…</h2><div class="task-meta" id="task-meta"></div><p id="task-description" class="goal-preview"></p><details class="task-goal"><summary>查看完整任务与来源</summary><pre id="task-goal"></pre></details></div>
<div class="tabs" role="tablist" aria-label="任务详情"><button id="tab-collaboration" class="tab active" role="tab" aria-selected="true" aria-controls="pane-collaboration">任务协作</button><button id="tab-samples" class="tab" role="tab" aria-selected="false" aria-controls="pane-samples">训练样本 <span id="tab-sample-count" class="tab-count">0</span></button></div>
<div id="pane-collaboration" class="tab-pane" role="tabpanel" aria-labelledby="tab-collaboration"><div class="section-head"><h3>谁参与了这次任务</h3><small id="collaboration-caption">依据实际运行记录</small></div><p class="subtle-copy" style="margin:-6px 0 16px">多智能体分工处理同一任务。点选角色，查看它实际留下的记录。</p><div id="collaboration-flow" class="flow"></div><div id="handoffs" class="handoffs"></div><p id="flow-note" class="flow-note"></p><div id="activity-timeline" class="activity-timeline"></div><div id="capture-note"></div><section class="collaboration-steps"><div class="role-run-heading"><h3 id="role-run-title">执行过程</h3><select id="role-run" class="run-picker" aria-label="选择运行记录" hidden></select></div><p id="role-run-description" class="subtle-copy" style="margin-bottom:14px"></p><div id="role-messages" class="timeline"></div><details id="role-diagnostics" class="technical-details" hidden><summary>查看此角色的采集记录</summary><pre id="role-source"></pre></details></section></div>
<div id="pane-samples" class="tab-pane" role="tabpanel" aria-labelledby="tab-samples" hidden><div class="section-head"><h3>可追溯的训练样本</h3><small id="sample-count"></small></div><p id="sample-preview-status" class="subtle-copy" role="status" style="margin-bottom:12px"></p><div class="samples-toolbar"><label class="search-field"><input id="task-query" placeholder="按任务编号筛选" aria-label="筛选任务编号"></label><select id="sample-kind" aria-label="样本类型"><option value="all">全部样本</option><option value="tools">工具过程</option><option value="chat">对话回合</option></select></div><div id="samples" class="sample-list"></div><div class="sample-inspector"><h3 id="sample-title">选择样本查看</h3><div id="sample-badges" class="sample-badges"></div><p id="sample-state" class="sample-state"></p><div id="sample-selection" style="margin-top:15px"><button id="select-sample-project" class="quiet-button" style="padding:0;color:var(--accent)">将此项目加入导出范围</button><span id="sample-selection-status" class="subtle-copy"></span></div><label class="sample-approval"><input id="approve" type="checkbox" disabled><span>我已核查此样本的任务结果，批准其质量</span></label><div id="messages" class="timeline"></div><details class="technical-details"><summary>工具定义与完整来源</summary><p id="sample-identity" class="sample-id"></p><h4 class="role" style="margin-top:14px">工具定义</h4><pre id="tools"></pre><h4 class="role" style="margin-top:14px">来源与质量依据</h4><pre id="source"></pre></details><details class="technical-details"><summary>训练格式预览</summary><label class="muted" style="display:block;font-size:11px;margin:12px 0">格式 <select id="format"><option value="hf">HF / TRL</option><option value="portable">通用 JSON</option></select></label><pre id="formats"></pre><p class="footnote">保留原始公开消息与工具协议。目标模型的 chat template 和训练配置需与格式匹配。</p></details></div></div></section></div>
<div class="status-line"><span id="load-status" role="status">正在读取服务器数据…</span><span>Argus · 团队内测</span></div><p id="error" role="alert"></p></main></div>
<div id="drawer-backdrop" class="drawer-backdrop" hidden></div><aside id="drawer" class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" hidden><div class="drawer-header"><h2 id="drawer-title">导出数据</h2><button id="close-drawer" class="drawer-close" aria-label="关闭面板">×</button></div>
<div id="drawer-export"><section class="drawer-section"><h3>选择范围</h3><div class="filter-row"><label>导出用途<select id="purpose"><option value="internal_training">内部训练</option><option value="external_sharing">第三方 / 商业分享</option></select></label><label>邀请码账号<input id="tenant" placeholder="全部账号" maxlength="160"></label></div><div id="export-projects" class="export-projects"></div><p id="review-summary">尚未选择项目。</p></section><section class="drawer-section"><h3>审核与授权</h3><label><input id="content-approved" type="checkbox">我已人工核查所选项目内容、隐私边界及适用范围。</label><label><input id="context-approved" type="checkbox">我已核查所批准工具样本的完整公开上下文、工具定义、参数与结果，确认不依赖未提供的系统指令。</label><label><input id="rights-approved" type="checkbox">我已另行核查第三方提供所需权利与上游许可（对外用途必选）。</label><label>独立验证报告 SHA-256（如有）<input id="evidence" maxlength="64" placeholder="64 位小写十六进制摘要"></label><button id="download" class="primary" disabled>校验并下载 ZIP</button><p id="export-status" role="status"></p><details><summary>审核类型与导出规则</summary><p>此处明确勾选的审阅记录为 human_operator。委托自动验收使用 automated_acceptance，并记录验证报告摘要，不标记为人工审核。</p><p>服务器在导出时重新检查授权、删除状态及样本资格。最终条数以包内 manifest 为准；导出不会启动训练、上传或出售。</p></details></section></div>
<div id="drawer-diagnostics" hidden><section class="drawer-section"><h3>采集状态与范围</h3><pre id="collector"></pre><details><summary>采集与格式限制</summary><pre id="limits"></pre></details></section><section class="drawer-section"><h3>采集缺口与排除记录</h3><p>用于定位未能进入训练样本的过程数据，独立于任务是否完成。</p><div id="reasons"></div><details><summary>完整诊断索引</summary><pre id="diagnostics"></pre></details></section></div>
<div id="drawer-audit" hidden><section class="drawer-section"><h3>最近导出记录</h3><p>自动验收与人工审核分别记录，可追溯到验证报告。</p><div class="table"><table><thead><tr><th>时间 / 操作</th><th>审阅 / 身份</th><th>结果 / 样本</th><th>证据摘要</th></tr></thead><tbody id="audit"></tbody></table></div></section></div></aside>
</body></html>"""

SCRIPT = r"""
'use strict';
const el=id=>document.getElementById(id);
let preview=null, overview=null, fallbackTasks=null, active=null, activeProject=null, activeTask=null, collaboration=null, activeRole=null;
let version=0, collaborationVersion=0, sampleVersion=0, readonly=true, busy=false, currentTab='collaboration', sampleLoadError='', sampleLoading=false;
const loadedProjects=new Map();
const selected=new Set(), approved=new Set();
const key=project=>JSON.stringify([project.tenant_id,project.sid]);
const projectName=project=>preview?.projects.find(row=>key(row)===key(project))?.title||project.title||'未命名任务';
const number=value=>value===null||value===undefined?'—':Number(value).toLocaleString();
const json=value=>JSON.stringify(value,null,2);
const roleLabels={manager:'统筹',planner:'规划',engineer:'执行',reviewer:'审查',operator:'用户',unknown:'角色未记录'};
const roleDescriptions={manager:'协调任务推进',planner:'拆解目标与计划',engineer:'执行、产出与验证',reviewer:'检查执行结果'};
const roleLabel=value=>roleLabels[String(value||'').toLowerCase()]||value||'未标注角色';
const cleanTitle=value=>String(value||'').replace(/^#+\s*/,'').split('\n')[0].slice(0,100);
const candidatesFor=project=>(preview?.candidates||[]).filter(row=>!project||key(row)===key(project));
const taskCandidates=task=>candidatesFor(task).filter(row=>!task||row.task_id===task.task_id||(!row.task_id&&!task.task_id));
const taskKey=task=>task.id||JSON.stringify([task.tenant_id,task.sid,task.task_id||null]);
const taskName=task=>cleanTitle(task.mission_title||task.title)||projectName(task);
const taskDescription=task=>task.request?.text||task.objective||'该轮原始请求未记录，可查看已保留的角色活动与执行过程。';
function tasksOnPage(){
  if(Array.isArray(overview?.tasks))return overview.tasks;
  if(Array.isArray(fallbackTasks))return fallbackTasks;
  return (preview?.projects||[]).flatMap(project=>{const candidates=candidatesFor(project),ids=[...new Set(candidates.map(row=>row.task_id||null))];
    return(ids.length?ids:[null]).map(task_id=>({...project,task_id,id:JSON.stringify([project.tenant_id,project.sid,task_id]),title:projectName(project),roles:[],quality:{approved_samples:candidates.filter(row=>row.task_id===task_id&&row.quality_approved).length},task_outcome:{state:'unknown',label:'任务结果未确认'}}));});
}
const toolCount=candidate=>(candidate?.sample?.messages||[]).reduce((count,message)=>count+(message.tool_calls?.length||0),0);
async function api(path,options={}) {
  const response=await fetch(path,{credentials:'same-origin',...options});
  const result=await response.json();
  if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'数据读取失败');
  return result;
}
function textNode(tag,text,className='') {
  const node=document.createElement(tag);node.textContent=text;node.className=className;return node;
}
function badge(label,tone='') {return textNode('span',label,'badge '+tone);}
function detailsBlock(title,body,className='message-summary') {
  const item=document.createElement('details');item.className=className;
  item.append(textNode('summary',title),textNode('pre',body));return item;
}
function selection(){return preview?preview.projects.filter(project=>project.eligible===true&&project.preview_loaded!==false&&selected.has(key(project))):[];}
function reviewed(){return preview?preview.candidates.filter(candidate=>approved.has(candidate.event_id)&&selected.has(key(candidate))):[];}
function setTab(tab){
  currentTab=tab;
  for(const name of ['collaboration','samples']){
    el('tab-'+name).className='tab'+(tab===name?' active':'');
    el('tab-'+name).setAttribute('aria-selected',String(tab===name));
    el('pane-'+name).hidden=tab!==name;
  }
}
let drawerFocus=null;
function openDrawer(kind){
  drawerFocus=document.activeElement||null;
  el('drawer-title').textContent={export:'导出数据',diagnostics:'采集诊断',audit:'导出与审计'}[kind];
  for(const name of ['export','diagnostics','audit'])el('drawer-'+name).hidden=name!==kind;
  el('drawer').hidden=false;el('drawer-backdrop').hidden=false;
  el('close-drawer').focus?.();
}
function closeDrawer(){el('drawer').hidden=true;el('drawer-backdrop').hidden=true;drawerFocus?.focus?.();}
function updateControls(){
  const projects=selection(),candidates=reviewed(),toolReview=candidates.some(candidate=>candidate.sample?.tools);
  const retainedReviews=(preview?.candidates||[]).filter(candidate=>candidate.quality_approved&&selected.has(key(candidate))&&!approved.has(candidate.event_id)).length;
  const digest=el('evidence').value.trim(),validDigest=!digest||/^[0-9a-f]{64}$/.test(digest);
  el('download').disabled=readonly||busy||!projects.length||!el('content-approved').checked||!validDigest||
    (toolReview&&!el('context-approved').checked)||(el('purpose').value==='external_sharing'&&!el('rights-approved').checked);
  el('approve').disabled=readonly||busy||!active||!selected.has(key(active));
  el('approve').checked=Boolean(active&&approved.has(active.event_id));
  const included=Boolean(active&&selected.has(key(active)));
  el('select-sample-project').hidden=!active||included;
  el('select-sample-project').disabled=readonly||busy||!active||!preview?.projects.some(project=>key(project)===key(active)&&project.eligible===true&&project.preview_loaded!==false);
  el('sample-selection-status').textContent=included?'此项目已加入导出范围':active?'加入导出范围后可逐条审核。':'';
  el('review-summary').textContent=number(projects.length)+' 个所选项目 · '+number(candidates.length)+' 条逐条批准候选 · '+number(retainedReviews)+' 条已有有效审阅凭据'+
    (readonly?' · 当前为只读会话':'')+'。最终合格条数在导出时重新核定。';
  el('prev').disabled=busy||!preview||!preview.offset;el('next').disabled=busy||!preview?.has_more_projects;
  for(const id of ['purpose','tenant','project-query','reload','content-approved','context-approved','rights-approved','evidence'])
    el(id).disabled=busy||(readonly&&['content-approved','context-approved','rights-approved','evidence'].includes(id));
  for(const input of document.querySelectorAll('.project-choice'))input.disabled=busy||input.dataset.eligible!=='true';
}
function clearReview(){for(const id of ['content-approved','context-approved','rights-approved'])el(id).checked=false;el('evidence').value='';}
function renderFormat(){
  if(!active){el('formats').textContent='';return;}
  const sample=structuredClone(active.sample);
  if(el('format').value==='portable')for(const message of sample.messages||[]){
    if(message.role==='tool')delete message.name;
    for(const call of message.tool_calls||[])call.function.arguments=JSON.stringify(call.function.arguments);
  }
  el('formats').textContent=json(sample);
}
function toolSummary(call){
  const name=call.function?.name||'工具',args=call.function?.arguments;
  const values=typeof args==='object'&&args?args:{};
  const label={bash:'执行命令',read:'读取文件',write:'写入文件',edit:'修改文件',grep:'查找内容',find:'查找文件',ls:'查看目录',web_search:'搜索资料',web_fetch:'读取网页'}[name]||name;
  let description=values.command||values.cmd||values.file_path||values.path||values.pattern||values.query||values.url||'';
  if(!values.command&&!values.cmd&&(values.file_path||values.path)&&typeof description==='string'){const parts=description.split('/').filter(Boolean);if(parts.length>2)description='…/'+parts.slice(-2).join('/');}
  return {label,description:typeof description==='string'?description.slice(0,160):json(description)};
}
function renderMessages(target,candidate){
  target.replaceChildren();
  const messages=candidate?.sample?.messages||[];
  if(!messages.length){target.append(textNode('div','尚无可展示的公开消息。','empty-state'));return;}
  const results=new Map(messages.filter(message=>message.role==='tool').map(message=>[message.tool_call_id,message]));
  let step=0,section=target;const totalCalls=toolCount(candidate);
  messages.forEach((message,index)=>{
    if(message.role==='tool')return;
    const content=message.content===undefined||message.content===null?'':typeof message.content==='string'?message.content:json(message.content);
    if(content){
      const final=message.role==='assistant'&&index===messages.length-1;
      const title=message.role==='user'?'模型输入（原始上下文）':final?'执行结果':'过程说明';
      (final?target:section).append(detailsBlock(title,content,'message-summary'+(final?' result-message':'')));
    }
    for(const call of message.tool_calls||[]){
      step+=1;
      if(step===7){const more=document.createElement('details');more.className='more-steps';const rest=document.createElement('div');rest.className='timeline';more.append(textNode('summary','展开其余 '+number(totalCalls-6)+' 次工具调用'),rest);target.append(more);section=rest;}
      const result=results.get(call.id),summary=toolSummary(call);
      const item=document.createElement('details');item.className='tool-step';
      const heading=document.createElement('summary'),copy=document.createElement('div');copy.className='step-heading';
      copy.append(textNode('strong',summary.label),textNode('small',summary.description||call.function?.name||'工具调用'));
      heading.append(textNode('span',String(step).padStart(2,'0'),'step-number'),copy,textNode('span',result?'✓':'·','step-check'),textNode('span','›','step-arrow'));
      const body=document.createElement('div');body.className='tool-body';
      body.append(textNode('h4','调用参数'),textNode('pre',typeof call.function?.arguments==='string'?call.function.arguments:json(call.function?.arguments)));
      body.append(textNode('h4',result?'真实返回结果':'未记录对应返回'),textNode('pre',result?(typeof result.content==='string'?result.content:json(result.content)):'这一步的返回数据未进入当前样本。'));
      body.append(textNode('small','调用编号：'+call.id));item.append(heading,body);section.append(item);
    }
  });
}
function inspect(candidate){
  active=candidate;
  el('sample-title').textContent=candidate?(activeTask&&activeTask.task_id===candidate.task_id&&key(activeTask)===key(candidate)?taskName(activeTask):projectName(candidate)):'选择样本查看';
  el('sample-identity').textContent=candidate?candidate.sid+' · '+candidate.tenant_id+' · 任务 '+(candidate.task_id||'未记录可靠任务 ID'):'';
  el('sample-badges').replaceChildren();
  for(const id of ['tools','source','sample-state'])el(id).textContent='';
  if(candidate){
    el('sample-badges').append(badge(candidate.sample?.tools?'工具过程':'对话回合','purple'),badge(candidate.quality_approved?'已有质量凭据':'待验证',candidate.quality_approved?'ready':''));
    if(candidate.sample?.tools)el('sample-badges').append(badge(number(toolCount(candidate))+' 次工具调用'));
    el('sample-state').textContent=candidate.quality_approved?'已有明确质量依据，可在审核授权后导出。':'已采集真实过程，尚待核查结果质量。';
    const evidence=candidate.quality_evidence;
    if(evidence?.reviewer_kind)el('sample-state').textContent+=' '+(evidence.reviewer_kind==='automated_acceptance'?'独立自动验收':evidence.reviewer_kind==='human_operator'?'人工审阅':'审阅方式：'+evidence.reviewer_kind)+'。';
    el('tools').textContent=candidate.sample?.tools?json(candidate.sample.tools):'此对话样本不包含工具定义。';
    el('source').textContent=json({task_id:candidate.task_id||null,project:candidate.sid,tenant:candidate.tenant_id,event_id:candidate.event_id,event_ids:candidate.event_ids,source:candidate.source,
      runtime:candidate.runtime||candidate.source?.runtime||null,runtime_profile:candidate.runtime_profile,scope:candidate.scope,quality_evidence:candidate.quality_evidence,
      sample_complete:candidate.sample_complete,model_context_complete:candidate.model_context_complete,global_complete:candidate.global_complete});
  }
  renderMessages(el('messages'),candidate);renderFormat();updateControls();
}
function renderSamples(){
  el('samples').replaceChildren();
  const query=el('task-query').value.trim(),kind=el('sample-kind').value||'all';
  const candidates=taskCandidates(activeTask).filter(candidate=>(!query||String(candidate.task_id||'').includes(query))&&(kind==='all'||(kind==='tools')===Boolean(candidate.sample?.tools)));
  el('sample-count').textContent=sampleLoading?'正在读取…':number(candidates.length)+' 条记录';el('tab-sample-count').textContent=sampleLoading?'…':number(taskCandidates(activeTask).length);
  const displayActive=active?.event_id||candidates.find(candidate=>candidate.quality_approved)?.event_id||candidates[0]?.event_id;
  candidates.forEach((candidate,index)=>{
    const button=document.createElement('button');button.className='sample'+(displayActive===candidate.event_id?' active':'');
    const role=candidate.source?.role||candidate.runtime?.role;
    button.append(textNode('strong',(role?roleLabel(role)+' · ':'')+(candidate.sample?.tools?'工具过程':'对话回合')+' '+String(index+1).padStart(2,'0')));
    button.append(textNode('small',candidate.sample?.tools?number(toolCount(candidate))+' 次工具调用':'公开消息'));
    button.append(badge(candidate.quality_approved?'已验证，可用于训练':'待验证',candidate.quality_approved?'ready':''));
    button.onclick=()=>{inspect(candidate);renderSamples();};el('samples').append(button);
  });
  if(active&&!candidates.some(candidate=>candidate.event_id===active.event_id))inspect(null);
  if(!active&&candidates.length)inspect(candidates.find(candidate=>candidate.quality_approved)||candidates[0]);
  if(!candidates.length){el('samples').append(textNode('div',sampleLoading?'正在读取此项目的训练样本…':'这次任务尚无符合当前范围的训练样本。','empty-state'));inspect(null);}
}
function projectCandidates(project){return candidatesFor(project);}
function renderProjects(){
  el('projects').replaceChildren();el('export-projects').replaceChildren();
  const mode=el('task-view').value||'all',query=el('project-query').value.trim().toLowerCase();
  const allTasks=tasksOnPage(),tasks=[...allTasks].filter(task=>mode==='activity'?!task.task_id:Boolean(task.task_id)).sort((a,b)=>(b.quality?.approved_samples||0)-(a.quality?.approved_samples||0)||(b.last_observed_at||0)-(a.last_observed_at||0));
  for(const project of preview?.projects||[]){
    const candidates=candidatesFor(project),ready=candidates.filter(candidate=>candidate.quality_approved).length;
    const input=document.createElement('input');input.type='checkbox';input.className='project-choice';input.dataset.eligible=String(project.eligible===true&&project.preview_loaded!==false);input.checked=selected.has(key(project));
    input.setAttribute('aria-label','选择 '+project.tenant_id+' / '+project.sid);
    input.onchange=()=>{if(input.checked)selected.add(key(project));else selected.delete(key(project));
      for(const candidate of preview.candidates)if(!selected.has(key(candidate)))approved.delete(candidate.event_id);
      clearReview();updateControls();};
    const label=document.createElement('label');label.className='export-project-label';const copy=textNode('span',projectName(project));
    copy.append(textNode('small',project.preview_loaded===false?'打开任务并读取样本后可选':project.eligible?'用途已授权 · '+number(ready)+' 条已有质量凭据':project.reason||'用途未授权'));label.append(input,copy);el('export-projects').append(label);
  }
  let shown=0;
  for(const task of tasks){
    const candidates=taskCandidates(task),ready=task.quality?.approved_samples??candidates.filter(row=>row.quality_approved).length;
    const search=[taskName(task),task.title,task.request?.text,task.task_id,task.sid,task.tenant_id].filter(Boolean).join(' ').toLowerCase();
    if((mode==='ready'&&!ready)||(mode==='pending'&&ready)||(query&&!search.includes(query)))continue;
    shown+=1;const item=document.createElement('article');item.className='project'+(activeTask&&taskKey(activeTask)===taskKey(task)?' active':'');
    const button=document.createElement('button');button.className='project-open';button.setAttribute('aria-label','查看任务 '+taskName(task));
    button.append(textNode('div',task.task_id?taskName(task):taskName(task)+' · 项目活动','project-title'));const meta=document.createElement('div');meta.className='project-meta';
    meta.append(badge(ready?'已有合格样本':candidates.length?'样本待验证':'运行观察记录',ready?'ready':candidates.length?'purple':''),textNode('small',task.tenant_id));
    button.append(meta);
    if(task.last_observed_at)button.append(textNode('div',new Date(task.last_observed_at*1000).toLocaleString('zh-CN', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}),'project-updated'));
    button.onclick=()=>selectTask(task);item.append(button);
    const source=document.createElement('details');source.className='project-source';source.append(textNode('summary','来源编号'));
    source.append(textNode('span',task.sid+' · '+task.tenant_id+(task.task_id?' · '+task.task_id:'')));item.append(source);el('projects').append(item);
  }
  const activities=allTasks.filter(task=>!task.task_id).length;
  el('project-count').textContent=number(shown)+(mode==='activity'?' 条项目活动':' 个任务')+(mode!=='activity'&&activities?' · '+number(activities)+' 组项目活动':'');
  if(!shown)el('projects').append(textNode('div','当前范围内没有任务。','empty-state'));
  updateControls();
}
function metrics(){
  el('metrics').replaceChildren();const data=preview;if(!data)return;
  const counts=overview?.counts,calls=data.candidates.reduce((sum,candidate)=>sum+toolCount(candidate),0);
  const values=[['真实任务',counts?.tasks??tasksOnPage().filter(task=>task.task_id).length,'本页具有明确关联的任务'],['参与协作的角色',counts?.roles??null,'本页实际观察到的角色类型'],['真实工具调用',counts?.tool_pairs??calls,'本页已配对的调用与返回'],['可用训练样本',counts?.approved_samples??data.counts.sft,'已有有效验收凭据']];
  for(const[label,value,caption]of values){const box=document.createElement('div');box.className='metric';box.append(textNode('div',label,'metric-label'),textNode('strong',number(value)),textNode('small',caption));el('metrics').append(box);}
  el('overview-scope').textContent='本页 '+number(data.projects.length)+' 个项目 · 合格样本优先';
  el('overview-limits').hidden=overview?.completeness?.page_truncated!==true;
  el('overview-limits').textContent=overview?.completeness?.page_truncated?'部分历史记录超出本页显示范围，当前统计仅覆盖已读取记录。':'';
}
function outcomeTag(state){
  const value=typeof state==='string'?state:state?.state||state?.status||'unknown';
  const labels={completed:'已完成',complete:'已完成',succeeded:'已完成',success:'已完成',accepted:'已完成',running:'执行中',in_progress:'执行中',paused:'已暂停',failed:'执行未完成',cancelled:'已取消',unknown:'结果待确认'};
  return badge(state?.label||labels[value]||value,['completed','complete','succeeded','success','accepted'].includes(value)?'ready':['running','in_progress'].includes(value)?'purple':'');
}
async function selectTask(task){
  activeTask=task;activeProject=preview?.projects.find(project=>key(project)===key(task))||task;activeRole=null;collaboration=null;
  sampleLoadError='';sampleLoading=true;sampleVersion+=1;inspect(null);renderProjects();renderSamples();renderTaskHeader();
  await Promise.all([loadCollaboration(task),loadProjectPreview(task)]);
}
function renderTaskHeader(){
  const task=activeTask,candidates=taskCandidates(task);el('task-outcome').replaceChildren();
  if(!task){el('task-title').textContent='选择一个任务';el('task-description').textContent='从左侧任务库开始，查看真实协作与采集到的样本。';el('task-meta').replaceChildren();el('task-goal').textContent='';return;}
  const prompt=task.request?.text||task.objective||'';
  el('task-title').textContent=taskName(task);el('task-description').textContent=taskDescription(task);
  el('task-category').textContent=task.task_id?(task.request?'已记录的任务请求':task.objective?'已记录的任务目标':'任务 / 真实运行'):'项目观察记录';
  const ready=task.quality?.approved_samples??candidates.filter(candidate=>candidate.quality_approved).length;
  const roles=(task.roles||[]).filter(role=>!['unknown','operator'].includes(role.role)).length;
  const calls=(task.roles||[]).reduce((total,role)=>total+(role.tool_pairs||0),0);
  el('task-meta').replaceChildren(textNode('span',task.tenant_id),textNode('span','·','separator'),textNode('span',number(roles)+' 个协作角色'),textNode('span','·','separator'),textNode('span',number(calls)+' 次工具调用'),textNode('span','·','separator'),textNode('span',number(ready)+' 条合格样本'));
  el('task-outcome').append(outcomeTag(task.task_outcome));
  el('task-goal').textContent=json({title:taskName(task),tenant_id:task.tenant_id,project:task.sid,task_id:task.task_id,request:task.request||null,objective:task.objective||null,mission_brief_source:task.mission_brief_source||null,request_truncated:task.request?.truncated||false,task_outcome:task.task_outcome,quality:task.quality,global_complete:task.global_complete??false});
}
function renderAudit(data){
  el('audit').replaceChildren();
  for(const event of data.events||[]){const row=document.createElement('tr');
    const reviewName={automated_acceptance:'自动验收',human_operator:'人工审核'}[event.reviewer_kind]||event.reviewer_kind||'未标注';
    for(const value of [new Date(event.created_at*1000).toLocaleString()+' · '+event.action,reviewName+' / '+event.actor,event.outcome+' / '+number(event.sft),event.evidence_sha256||'未提供'])row.append(textNode('td',value));el('audit').append(row);}
  if(!(data.events||[]).length){const row=document.createElement('tr'),cell=textNode('td','暂无导出记录');cell.setAttribute('colspan','4');row.append(cell);el('audit').append(row);}
}
function renderProjectDiagnostics(data){
  el('limits').textContent=json(data.limits||[]);el('reasons').replaceChildren();
  for(const[reason,count]of Object.entries(data.reason_counts||{})){const row=document.createElement('div');row.className='exclusion-row';row.append(textNode('span',reason,'reason-label'),textNode('strong',number(count)));el('reasons').append(row);}
  if(!Object.keys(data.reason_counts||{}).length)el('reasons').append(textNode('p','当前项目没有排除记录。','muted'));
  el('diagnostics').textContent=json({project:activeTask?{tenant_id:activeTask.tenant_id,sid:activeTask.sid}:null,records:data.diagnostics||[],total:data.diagnostics_total,truncated:data.diagnostics_truncated});
}
function refreshLoadedPreview(){
  if(!preview)return;
  preview.candidates=[...loadedProjects.values()].flatMap(data=>data.candidates);
  preview.projects=preview.projects.map(project=>{
    const loaded=loadedProjects.get(key(project));
    return {...project,...(loaded?.project||{}),preview_loaded:Boolean(loaded)};
  });
  for(const project of preview.projects)if(project.eligible!==true||project.preview_loaded===false)selected.delete(key(project));
  for(const candidate of preview.candidates)if(!selected.has(key(candidate)))approved.delete(candidate.event_id);
}
async function loadProjectPreview(task){
  const current=++sampleVersion,taskIdentity=taskKey(task),projectIdentity=key(task);
  el('sample-preview-status').textContent='正在读取此项目的训练样本…';sampleLoadError='';sampleLoading=true;
  try{
    let data=loadedProjects.get(projectIdentity);
    if(!data){
      const params=new URLSearchParams({purpose:el('purpose').value||'internal_training',tenant:task.tenant_id,query:task.sid});
      const result=await api('/admin/api/training/preview?'+params);
      if(current!==sampleVersion||!activeTask||taskKey(activeTask)!==taskIdentity)return;
      if(!Array.isArray(result.projects)||!Array.isArray(result.candidates))throw Error('训练样本格式不正确');
      const project=result.projects.find(row=>key(row)===projectIdentity);
      if(!project)throw Error('该项目不在当前授权预览范围内。');
      data={...result,project,candidates:result.candidates.filter(row=>key(row)===projectIdentity)};
      loadedProjects.set(projectIdentity,data);
    }
    if(current!==sampleVersion||!activeTask||taskKey(activeTask)!==taskIdentity)return;
    // Retain selected export projects and the current project; other payloads can be reloaded on demand.
    for(const cachedKey of loadedProjects.keys())if(cachedKey!==projectIdentity&&!selected.has(cachedKey))loadedProjects.delete(cachedKey);
    sampleLoading=false;refreshLoadedPreview();renderProjects();renderSamples();renderProjectDiagnostics(data);
    el('sample-preview-status').textContent='已读取当前项目 · '+number(taskCandidates(task).length)+' 条任务样本';
    if(collaboration)renderCollaboration();updateControls();
  }catch(error){
    if(current!==sampleVersion||!activeTask||taskKey(activeTask)!==taskIdentity)return;
    sampleLoading=false;sampleLoadError=error.message==='training_source_size_limit'?'此项目的样本超过单次读取上限，协作记录仍可查看。':'此项目的训练样本暂不可读取：'+error.message;
    el('sample-preview-status').textContent=sampleLoadError;renderSamples();
    if(collaboration)renderCollaboration();updateControls();
  }
}
async function load(offset=0){
  if(busy)return;
  const previousKey=activeTask?taskKey(activeTask):null,current=++version;collaborationVersion+=1;sampleVersion+=1;
  selected.clear();approved.clear();loadedProjects.clear();clearReview();preview=null;overview=null;fallbackTasks=null;activeProject=null;activeTask=null;collaboration=null;sampleLoadError='';sampleLoading=false;inspect(null);
  el('sample-preview-status').textContent='';el('load-status').textContent='正在读取协作概览…';el('error').textContent='';el('connection-label').textContent='连接中';el('connection').dataset.state='loading';
  const params=new URLSearchParams({purpose:el('purpose').value||'internal_training',offset:String(offset)});
  if(el('tenant').value.trim())params.set('tenant',el('tenant').value.trim());
  try{
    const[taskData,identity,collector,audit]=await Promise.all([api('/admin/api/training/collaboration?'+params),api('/invite/status'),
      api('/admin/api/research/status').catch(error=>({state:'unavailable',detail:error.message})),api('/admin/api/training/audit').catch(error=>({events:[],error:error.message}))]);
    if(current!==version)return;
    readonly=identity.role!=='admin'||identity.readonly!==false;overview=taskData;
    if(Array.isArray(taskData.tasks)&&Array.isArray(taskData.projects)){
      preview={projects:taskData.projects.map(project=>({...project,preview_loaded:false})),candidates:[],counts:{},
        offset:taskData.offset||0,selection_limit:taskData.selection_limit||20,total_projects:taskData.total_projects??taskData.projects.length,
        has_more_projects:taskData.has_more_projects===true,next_offset:taskData.next_offset};
    }else{
      // Compatibility with deployments that only expose the previous preview contract.
      const legacy=await api('/admin/api/training/preview?'+params);
      if(current!==version)return;
      if(!Array.isArray(legacy.projects)||!Array.isArray(legacy.candidates)||!legacy.counts)throw Error('协作概览格式不正确');
      preview={...legacy,projects:[...legacy.projects],candidates:[...legacy.candidates]};overview=null;fallbackTasks=tasksOnPage();
      for(const project of legacy.projects)loadedProjects.set(key(project),{...legacy,project,candidates:legacy.candidates.filter(row=>key(row)===key(project))});
      refreshLoadedPreview();
    }
    el('session-label').textContent=readonly?'只读会话':'私有工作空间';metrics();renderProjects();
    el('page').textContent='第 '+number(Math.floor(preview.offset/preview.selection_limit)+1)+' 页';
    el('collector').textContent=json({research_journal:collector,collaboration_limitations:taskData.limitations||null,completeness:taskData.completeness||null});renderProjectDiagnostics({});renderAudit(audit);
    if(audit.error)el('error').textContent='审计读取失败：'+audit.error;
    const tasks=tasksOnPage(),task=tasks.find(row=>taskKey(row)===previousKey)||tasks.find(row=>row.quality?.approved_samples)||tasks.find(row=>row.task_id)||tasks[0];
    el('load-status').textContent='协作概览已同步 · 样本按项目读取';el('connection-label').textContent='已连接';el('connection').dataset.state='ready';
    if(task)await selectTask(task);else{renderTaskHeader();renderSamples();renderCollaboration();}
    if(current!==version)return;updateControls();
  }catch(error){if(current!==version)return;el('error').textContent=error.message;el('load-status').textContent='协作概览读取失败，请重试。';el('connection-label').textContent='连接中断';el('connection').dataset.state='error';}
}
// Role labels and associations come only from the purpose-gated factual projection.
async function loadCollaboration(task){
  const current=++collaborationVersion;
  el('collaboration-flow').replaceChildren(textNode('div','正在读取实际协作记录…','empty-state'));
  el('handoffs').replaceChildren();el('role-messages').replaceChildren();el('capture-note').replaceChildren();
  try{
    const params=new URLSearchParams({purpose:el('purpose').value||'internal_training'});if(task.task_id)params.set('task_id',task.task_id);
    const result=await api('/admin/api/training/collaboration/'+encodeURIComponent(task.tenant_id)+'/'+encodeURIComponent(task.sid)+'?'+params);
    if(current!==collaborationVersion||!activeTask||taskKey(task)!==taskKey(activeTask))return;
    collaboration=result;renderCollaboration();
  }catch(error){if(current!==collaborationVersion)return;collaboration={error:error.message};renderCollaboration();}
}
function firstRoleObservation(role){
  const stamps=(collaboration?.segments||[]).filter(segment=>segment.role===role&&typeof segment.started_at==='number').map(segment=>segment.started_at);
  return stamps.length?Math.min(...stamps):Infinity;
}
function collaborationRoles(){
  return [...(collaboration?.roles||[])].filter(role=>!['unknown','operator'].includes(role.role)).sort((a,b)=>firstRoleObservation(a.role)-firstRoleObservation(b.role));
}
function renderActivityTimeline(roles){
  el('activity-timeline').replaceChildren();
  const chronological=[...(collaboration?.segments||[])].filter(segment=>roles.some(role=>role.role===segment.role)&&typeof segment.started_at==='number').sort((a,b)=>a.started_at-b.started_at),seen=new Set();
  for(const segment of chronological){
    if(seen.has(segment.role))continue;seen.add(segment.role);
    const row=document.createElement('button');row.className='activity-row';row.onclick=()=>{activeRole=segment.role;renderCollaboration();};
    row.append(textNode('span',new Date(segment.started_at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}),'activity-time'),textNode('span','','activity-marker'),textNode('strong',segment.label||roleLabel(segment.role)),textNode('span',segment.summary||'角色活动','activity-summary'));
    el('activity-timeline').append(row);
  }
}
function renderCollaboration(){
  const roles=collaborationRoles(),candidates=taskCandidates(activeTask),episodes=collaboration?.episodes||[];
  el('collaboration-flow').replaceChildren();el('handoffs').replaceChildren();el('capture-note').replaceChildren();el('activity-timeline').replaceChildren();
  el('flow-note').textContent='';el('role-source').textContent='';el('role-diagnostics').hidden=true;
  el('collaboration-caption').textContent=roles.length?number(roles.length)+' 个实际参与的角色':'依据实际运行记录';
  if(!roles.length){
    const block=document.createElement('div');block.className='empty-state';block.append(textNode('strong',collaboration?.error?'协作记录暂时不可用':'暂无可确认的角色协作记录'),textNode('span',collaboration?.error?'可以先在「训练样本」查看已采集的工具过程。':'当前样本只说明已保留的执行过程，不推断其他角色参与。'));el('collaboration-flow').append(block);
    el('role-run-title').textContent='已采集的公开过程';el('role-run-description').textContent=candidates.length?'选择「训练样本」查看消息、工具参数与真实返回。':'';
    el('role-run').hidden=true;el('role-messages').replaceChildren();return;
  }
  if(!activeRole||!roles.some(role=>role.role===activeRole))activeRole=episodes.find(episode=>episode.quality_approved&&roles.some(role=>role.role===episode.role))?.role||roles[0].role;
  roles.forEach(role=>{
    const ownEpisodes=episodes.filter(episode=>episode.role===role.role),gaps=ownEpisodes.filter(episode=>episode.state==='quarantined').length;
    const ready=ownEpisodes.filter(episode=>episode.quality_approved).length;
    const button=document.createElement('button');button.className='role-card'+(activeRole===role.role?' active':'');
    const top=document.createElement('div');top.className='role-card-top';const copy=document.createElement('div');
    copy.append(textNode('div',role.label||roleLabel(role.role),'role-name'),textNode('div',role.role,'role-caption'));
    const symbols={manager:'统',planner:'规',engineer:'执',reviewer:'审'};
    top.append(textNode('span',symbols[role.role]||'·','role-symbol'),copy);button.append(top);
    button.append(textNode('div',roleDescriptions[role.role]||'参与任务处理','role-caption'));
    button.append(badge(ready?'已有合格样本':gaps?'采集有缺口':role.observations?'已有活动记录':'已参与',ready?'ready':gaps?'amber':''));
    const meta=document.createElement('div');meta.className='role-card-meta';
    if(gaps&&!role.tool_pairs)meta.append(textNode('span','工具详情未保留'));
    else meta.append(textNode('strong',number(role.tool_pairs)),textNode('span',' 次可查看调用'));
    button.append(meta);
    const observed=(collaboration?.segments||[]).filter(segment=>segment.role===role.role&&segment.started_at).map(segment=>segment.started_at);
    if(observed.length)button.append(textNode('div',new Date(Math.min(...observed)*1000).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit',hour12:false})+' 首条记录 · '+number(role.observations)+' 条观察','role-caption'));
    button.onclick=()=>{activeRole=role.role;renderCollaboration();};el('collaboration-flow').append(button);
  });
  for(const edge of collaboration?.handoffs||[]){
    const from=roles.find(role=>role.role===edge.from),to=roles.find(role=>role.role===edge.to);
    if(from&&to)el('handoffs').append(textNode('span',from.label+' → '+to.label+(edge.label?' · '+edge.label:''),'handoff'));
  }
  el('flow-note').textContent=(collaboration?.handoffs||[]).length?'展示有来源记录的角色交接。':'按首条记录的时间排列；未记录明确交接关系。';
  renderActivityTimeline(roles);
  const complete=collaboration?.collection?.accepted_episodes||0,gaps=collaboration?.collection?.quarantined_episodes||0;
  if(gaps){
    const note=document.createElement('div');note.className='callout';const copy=document.createElement('div');
    copy.append(textNode('strong',number(complete)+' 段过程已采集 · '+number(gaps)+' 段存在采集缺口'),textNode('p','训练样本按单段执行过程验收。角色参与、任务结果与整个协作过程是否采全，分别记录。'));
    note.append(copy);el('capture-note').append(note);
  }
  renderRole(roles.find(role=>role.role===activeRole)||roles[0]);
  setTimeout(()=>{const card=[...(el('collaboration-flow').children||[])].find(node=>node.className.includes(' active'));if(card&&Number.isFinite(card.offsetLeft))el('collaboration-flow').scrollLeft=Math.max(0,card.offsetLeft-el('collaboration-flow').offsetLeft-10);},0);
}
function renderObservedSegments(target,segments){
  for(const[index,segment]of segments.entries()){
    const item=document.createElement('details');item.className='tool-step';const summary=document.createElement('summary'),copy=document.createElement('div');copy.className='step-heading';
    copy.append(textNode('strong',segment.summary||'角色活动'),textNode('small',segment.started_at?new Date(segment.started_at*1000).toLocaleString():'时间未记录'));
    summary.append(textNode('span',String(index+1).padStart(2,'0'),'step-number'),copy,textNode('span','›','step-arrow'));
    const body=document.createElement('div');body.className='tool-body';body.append(textNode('pre',json(segment)));item.append(summary,body);target.append(item);
  }
}
function renderRole(role){
  const label=role.label||roleLabel(role.role),episodes=(collaboration?.episodes||[]).filter(episode=>episode.role===role.role);
  const segments=(collaboration?.segments||[]).filter(segment=>segment.role===role.role&&segment.source_kind!=='tool_episode');
  const ids=new Set(episodes.map(episode=>episode.sample_event_id));
  const candidates=taskCandidates(activeTask).filter(candidate=>ids.has(candidate.event_id));
  el('role-run-title').textContent=label+' · 执行过程';
  el('role-run-description').textContent=number(role.observations)+' 条运行观察 · '+number(episodes.length)+' 段采集过程'+(candidates.length?' · 点击步骤查看真实参数与返回':'');
  el('role-run').replaceChildren();el('role-run').hidden=candidates.length<2;
  candidates.forEach((candidate,index)=>{const option=textNode('option','过程 '+(index+1)+' · '+toolCount(candidate)+' 次调用'+(candidate.quality_approved?' · 已验证':''));option.value=candidate.event_id;el('role-run').append(option);});
  const chosen=candidates.find(candidate=>candidate.quality_approved)||candidates[0];
  if(sampleLoading)el('role-messages').replaceChildren(textNode('div','正在读取此项目的工具详情…','empty-state'));
  else if(chosen){el('role-run').value=chosen.event_id;renderMessages(el('role-messages'),chosen);}
  else{
    el('role-messages').replaceChildren();
    const gap=episodes.some(episode=>episode.state==='quarantined');
    if(segments.length){
      if(gap)el('role-messages').append(textNode('p','已确认该角色参与。以下是保留的活动摘要；工具参数与返回内容未保留。','retained-notice'));
      renderObservedSegments(el('role-messages'),segments.slice(0,15));
    }else el('role-messages').append(textNode('div',sampleLoadError|| (gap?'已确认该角色参与。此段过程存在采集缺口，未进入可查看的训练样本。':'已保留该角色的参与记录，尚无可展示的工具过程。'),'empty-state'));
  }
  el('role-run').onchange=()=>renderMessages(el('role-messages'),candidates.find(row=>row.event_id===el('role-run').value));
  el('role-source').textContent=json({role,episodes,segments,unassigned_observations:collaboration?.unassigned_observations,global_complete:collaboration?.global_complete});el('role-diagnostics').hidden=false;
}
el('approve').onchange=()=>{if(!active||readonly||!selected.has(key(active)))return;if(el('approve').checked)approved.add(active.event_id);else approved.delete(active.event_id);el('context-approved').checked=false;updateControls();};
for(const id of ['content-approved','context-approved','rights-approved','evidence'])el(id).oninput=updateControls;
for(const id of ['task-query','sample-kind'])el(id).oninput=renderSamples;
el('select-sample-project').onclick=()=>{if(el('select-sample-project').disabled||!active)return;selected.add(key(active));clearReview();renderProjects();updateControls();};
el('task-view').onchange=renderProjects;el('format').onchange=renderFormat;el('reload').onclick=()=>load();
for(const id of ['purpose','tenant'])el(id).onchange=()=>load();
el('project-query').oninput=renderProjects;
el('prev').onclick=()=>load(Math.max(0,preview.offset-preview.selection_limit));el('next').onclick=()=>load(preview.next_offset);
el('tab-collaboration').onclick=()=>setTab('collaboration');el('tab-samples').onclick=()=>setTab('samples');
el('open-export').onclick=()=>openDrawer('export');el('open-diagnostics').onclick=()=>openDrawer('diagnostics');el('nav-audit').onclick=()=>openDrawer('audit');
el('close-drawer').onclick=closeDrawer;el('drawer-backdrop').onclick=closeDrawer;
window.addEventListener('keydown',event=>{
  if(el('drawer').hidden)return;
  if(event.key==='Escape'){closeDrawer();return;}
  if(event.key==='Tab'){
    const focusable=[...(el('drawer').querySelectorAll?.('button:not([disabled]),input:not([disabled]),select:not([disabled]),summary,a[href]')||[])].filter(node=>node.getClientRects?.().length);
    const first=focusable[0],last=focusable[focusable.length-1];
    if(first&&event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(last&&!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }
});
el('download').onclick=async()=>{
  updateControls();if(el('download').disabled)return;
  const offset=preview.offset;let completed=false;
  const purpose=el('purpose').value,projects=selection().map(project=>({tenant_id:project.tenant_id,sid:project.sid}));
  const review={reviewer_kind:'human_operator',content_approved:el('content-approved').checked,tool_context_approved:el('context-approved').checked,rights_reviewed:el('rights-approved').checked,approved_event_ids:reviewed().map(candidate=>candidate.event_id)};
  if(el('evidence').value.trim())review.evidence_sha256=el('evidence').value.trim();
  busy=true;updateControls();el('export-status').textContent='正在校验授权与样本，并生成数据包…';
  try{
    const response=await fetch('/admin/api/training/export',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({purpose,projects,review})});
    if(!response.ok){const error=await response.json();throw Error(error.detail||'导出失败');}
    if(!response.headers.get('content-type')?.includes('application/zip'))throw Error('服务器未返回 ZIP');
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='argus-'+purpose+'-review.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    el('export-status').textContent='数据包已下载。实际样本条数、来源与审核证据见包内 manifest。';completed=true;
  }catch(error){el('export-status').textContent='未完成导出：'+error.message;}
  finally{busy=false;updateControls();}
  if(completed)await load(offset);
};
load();
"""
