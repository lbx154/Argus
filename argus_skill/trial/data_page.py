"""Private, task-centred workbench for genuine collaboration and training records."""

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Argus · 数据工作台</title>
<script src="/admin/data/app.js" defer></script><style>
:root{color-scheme:light;font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#262934;background:#f7f8fa;--ink:#262934;--muted:#858995;--line:#e9eaf0;--accent:#635bca;--accent-soft:#f0eefa;--green:#298570;--green-soft:#eaf6f0;--amber:#ad7c35;--amber-soft:#fbf3e4}
*{box-sizing:border-box}body{margin:0}button,input,select{font:inherit}a{color:inherit;text-decoration:none}button{cursor:pointer}button:disabled{opacity:.42;cursor:default}button,input,select{border:1px solid var(--line);border-radius:8px;background:white;color:var(--ink);padding:8px 11px}button:hover:not(:disabled){border-color:#c8c4e9;background:#faf9ff}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #a7a0ed;outline-offset:3px}input[type=checkbox]{accent-color:var(--accent);width:15px;height:15px;margin:2px 7px 0 0;vertical-align:top}h1,h2,h3,h4,p{margin:0}h1{font-size:25px;letter-spacing:-.7px;font-weight:650}h2{font-size:19px;letter-spacing:-.35px;font-weight:650}h3{font-size:14px;font-weight:650}small,.muted{color:var(--muted)}small{font-size:12px}.icon{width:19px;height:19px;display:inline-flex;align-items:center;justify-content:center;flex:none}.icon svg{width:100%;height:100%;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}.sidebar{position:fixed;inset:0 auto 0 0;width:205px;background:#fbfbfd;border-right:1px solid var(--line);padding:29px 18px;display:flex;flex-direction:column;z-index:10}.brand{display:flex;align-items:center;gap:10px;font-size:20px;letter-spacing:2px;font-weight:720;margin:0 10px 44px}.brand-mark{display:grid;place-items:center;background:var(--accent);color:white;width:30px;height:32px;border-radius:9px;font-size:19px;font-weight:600;letter-spacing:-3px;padding-right:3px}.nav-caption{font-size:10px;color:#a3a5af;letter-spacing:1.4px;padding:0 12px;margin:0 0 12px}.nav-link{border:0;background:none;display:flex;align-items:center;text-align:left;gap:10px;padding:11px 12px;width:100%;margin:3px 0;font-size:13px;color:#7b7f8c;border-radius:8px}.nav-link.active{background:var(--accent-soft);color:var(--accent);font-weight:620}.nav-link:hover{background:#f0f0f5}.sidebar-bottom{margin-top:auto}.operator{border-top:1px solid var(--line);margin:20px 10px 0;padding-top:20px;display:flex;align-items:center;gap:10px;font-size:12px}.avatar{width:32px;height:32px;background:#efedf6;color:var(--accent);display:grid;place-items:center;border-radius:50%;font-size:11px;font-weight:650}.operator small{display:block;font-size:10px}.shell{margin-left:205px}.topbar{height:72px;border-bottom:1px solid var(--line);padding:0 34px;display:flex;align-items:center;justify-content:space-between;background:#fff;font-size:12px;color:var(--muted)}.breadcrumb{display:flex;align-items:center;gap:13px}.breadcrumb strong{color:#454955;font-weight:500}.top-actions{display:flex;align-items:center;gap:12px}.connection{display:flex;align-items:center;gap:7px;font-size:11px}.connection[data-state=error] .status-dot{background:#c48b62}.connection[data-state=loading] .status-dot{background:#b6b1c7}.status-dot{width:6px;height:6px;border-radius:50%;background:#45a183}.quiet-button{background:transparent;border:0;color:var(--muted);font-size:12px;padding:6px}.button-icon{display:inline-flex;align-items:center;justify-content:center;gap:7px}.primary{background:var(--accent);color:white;border-color:var(--accent);font-weight:550}.primary:hover:not(:disabled){background:#544cb7;border-color:#544cb7}.main{max-width:1660px;margin:auto;padding:32px 34px 50px}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:25px}.page-heading p{color:var(--muted);font-size:12px;margin-top:5px}.page-heading>div:first-child{flex:1;min-width:0}.page-heading .actions{display:flex;gap:8px;flex:none}.page-heading .actions button{white-space:nowrap}.metrics{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);background:white;border-radius:12px;margin-bottom:28px;padding:23px 0}.metric{padding:0 24px;border-right:1px solid var(--line)}.metric:last-child{border:0}.metric-label{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:7px}.metric strong{font-size:29px;font-weight:630;line-height:1.3;display:block;letter-spacing:-.8px;margin:8px 0 3px}.metric small{font-size:10px}.metric:last-child strong{color:var(--green)}.library-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:13px}.library-heading h2{font-size:15px}.scope-label{font-size:11px;color:#9296a1}.workspace{display:grid;grid-template-columns:300px minmax(0,1fr);border:1px solid var(--line);border-radius:13px;background:#fff;min-height:700px;overflow:hidden}#projects{max-height:800px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#dedde8 transparent}.task-library{border-right:1px solid var(--line);background:#fdfdfe;display:flex;flex-direction:column;min-width:0}.search-wrap{padding:18px 16px 12px}.search-field{position:relative;display:flex;align-items:center;gap:8px;background:#f6f7f9;border:1px solid #eeeef3;border-radius:7px;padding-left:10px;color:#9599a4}.search-field input{background:none;border:0;border-radius:0;width:100%;outline:none;font-size:12px;padding:8px 4px}.search-field .icon{width:15px;height:15px}.list-toolbar{display:flex;align-items:center;justify-content:space-between;padding:0 18px 12px;font-size:11px;color:var(--muted)}.list-toolbar select{font-size:11px;border:0;background:none;color:#737786;padding:2px 0}.project{border-top:1px solid #f0f1f5;position:relative}.project:first-child{border-top:0}.project.active{background:#f2f0fb}.project.active:before{content:"";position:absolute;left:0;top:15px;bottom:15px;width:3px;background:var(--accent);border-radius:0 3px 3px 0}.project-open{border:0;border-radius:0;background:none;text-align:left;width:100%;padding:17px 18px}.project-open:hover:not(:disabled){background:#f7f6fc}.project.active .project-open:hover{background:#f2f0fb}.project-title{font-size:13px;font-weight:580;line-height:1.55;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;margin-bottom:9px}.project-meta{display:flex;align-items:center;justify-content:space-between;gap:7px}.project-meta small{font-size:10px}.project-updated{font-size:9px;color:#a5a8b3;margin-top:7px}.tag,.badge{display:inline-flex;align-items:center;gap:5px;font-size:10px;line-height:1.4;padding:4px 7px;background:#f1f2f5;color:#8a8d97;border-radius:5px;white-space:nowrap;font-weight:500}.tag.ready,.badge.ready{color:var(--green);background:var(--green-soft)}.tag.purple,.badge.purple{background:var(--accent-soft);color:var(--accent)}.tag.amber,.badge.amber{background:var(--amber-soft);color:var(--amber)}.project-source{display:none;margin:0 18px 10px;font-size:10px;color:#9497a2}.project-source summary{font-size:10px}.project-source span{display:block;word-break:break-all;padding:5px 0}.paging{display:flex;align-items:center;justify-content:space-between;gap:7px;padding:16px;margin-top:auto;border-top:1px solid var(--line)}.paging button{font-size:10px;padding:4px 6px;border:0;background:none;color:#8c8f9a}.paging span{font-size:10px;color:#979aa5}.task-detail{min-width:0}.detail-heading{padding:26px 28px 19px}.detail-topline{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:13px}.detail-kicker{font-size:10px;color:#9699a4;letter-spacing:.4px}.detail-heading h2{font-size:21px;font-weight:640;line-height:1.5;margin-bottom:9px}.task-meta{display:flex;align-items:center;gap:13px;flex-wrap:wrap;font-size:11px;color:#8b8f9b}.task-meta .separator{color:#d4d6dd}.goal-preview{font-size:12px;line-height:1.8;color:#808591;margin-top:13px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.observation-scope-label{display:flex;align-items:center;gap:8px;font-size:11px;color:#8a8e9c;margin-top:14px}.observation-scope-label select{font-size:11px;padding:5px 8px}.task-goal{margin-top:12px;font-size:11px;color:#8b8f9b}.tabs{display:flex;gap:26px;border-bottom:1px solid var(--line);padding:0 28px}.tab{border:0;background:none;border-radius:0;color:#9497a2;font-size:12px;padding:12px 0 14px;position:relative}.tab.active{color:var(--accent);font-weight:620}.tab.active:after{position:absolute;content:"";bottom:-1px;left:0;right:0;background:var(--accent);height:2px}.tab-count{font-size:10px;background:#f0eef9;border-radius:4px;padding:1px 5px;margin-left:5px}.tab-pane{padding:25px 28px 30px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:15px;margin-bottom:16px}.section-head h3{font-size:13px}.section-head small{font-size:10px}.subtle-copy{color:#9598a3;font-size:11px;line-height:1.8}.flow{display:flex;gap:10px;align-items:stretch;overflow-x:auto;padding:2px 1px 10px;margin-bottom:5px}.role-card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 13px;min-width:145px;flex:1;position:relative;text-align:left;white-space:normal}.role-card.active{border-color:#b7b0e5;background:#fbfaff;box-shadow:0 0 0 2px #f5f2fd}.role-card-top{display:flex;gap:8px;align-items:center;margin-bottom:11px}.role-symbol{width:29px;height:29px;display:grid;place-items:center;border-radius:8px;background:#f0eefb;color:#776fc3;font-size:13px;font-weight:550}.role-card:nth-child(3n+2) .role-symbol{background:#edf3f9;color:#6285aa}.role-card:nth-child(3n) .role-symbol{background:#ecf5ef;color:#6f9680}.role-name{font-size:12px;font-weight:590}.role-caption{font-size:9px;color:#9a9da8;margin-top:1px}.role-card .badge{margin-top:8px}.role-card .tag{font-size:9px;padding:3px 5px;margin-bottom:8px}.role-card-meta{font-size:10px;color:#9195a0}.role-card-meta strong{font-weight:600;color:#606571}.flow-note{font-size:10px;color:#9b9eaa;margin-top:8px;line-height:1.8}.handoffs{display:flex;flex-wrap:wrap;gap:7px;margin:10px 0}.handoff{font-size:10px;color:#8f8aaa;padding:5px 8px;background:#f8f7fc;border:1px solid #eeebf6;border-radius:5px}.callout{padding:13px 15px;border:1px solid #edeaf6;border-radius:8px;background:#faf9fd;display:flex;align-items:flex-start;gap:10px;margin-top:18px}.callout .icon{color:#9d97c5;width:16px;height:16px;margin-top:2px}.callout strong{display:block;font-size:11px;color:#70678f;font-weight:550;margin-bottom:2px}.callout p{font-size:10px;color:#9690a7;line-height:1.7}.collaboration-steps{margin-top:26px;border-top:1px solid var(--line);padding-top:23px}.role-run-heading{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:16px}.role-run-heading h3{font-size:13px}.run-picker{font-size:10px;padding:4px 6px;max-width:220px}.empty-state{padding:45px 20px;text-align:center;color:#9599a5;font-size:12px;line-height:1.9}.empty-state strong{display:block;color:#767b89;font-weight:500;margin-bottom:6px}.empty-state .icon{width:34px;height:34px;color:#c9c5df;margin:0 auto 10px}.more-steps{margin:0 0 12px}.more-steps>summary{font-size:11px;color:var(--accent);cursor:pointer;padding:8px 0 13px}.retained-notice{font-size:11px;line-height:1.9;color:#978367;background:#fcf8ef;border:1px solid #f0e8d8;border-radius:8px;padding:11px 13px;margin:0 0 15px}.activity-timeline{margin:15px 0 0;padding:12px 14px;background:#fafafd;border:1px solid #eeedf4;border-radius:8px}.activity-timeline:empty{display:none}.activity-row{border:0;background:none;width:100%;padding:6px 0;display:flex;align-items:center;gap:10px;text-align:left;font-size:10px}.activity-row:hover:not(:disabled){background:#f2f0f8;border:0}.activity-time{color:#9a9cab;font-variant-numeric:tabular-nums}.activity-marker{width:5px;height:5px;border-radius:50%;background:#b7b1d7;flex:none}.activity-row strong{font-weight:550;color:#7c7598}.activity-summary{color:#9694a4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.timeline{display:flex;flex-direction:column;gap:0}.tool-step{position:relative;border:1px solid #e9eaf0;border-radius:8px;margin:0 0 10px;background:white;overflow:hidden}.tool-step summary{display:flex;align-items:center;gap:10px;cursor:pointer;padding:12px 13px;list-style:none}.tool-step summary::-webkit-details-marker{display:none}.step-number{font-size:9px;color:#9397a3;background:#f4f4f7;border:1px solid #eeeeF3;border-radius:5px;width:22px;height:22px;display:grid;place-items:center;flex:none}.step-heading{flex:1;min-width:0}.step-heading strong{display:block;font-size:11px;font-weight:550;color:#555b69;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}.step-heading small{font-size:10px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;margin-top:1px}.step-check{color:#6ba590;font-size:12px}.step-arrow{color:#b0b3bd;font-size:13px;margin-left:4px}.tool-step[open] .step-arrow{transform:rotate(90deg)}.tool-body{border-top:1px solid #eeeef4;padding:14px 15px;background:#fcfcfe}.tool-body h4{font-size:10px;font-weight:550;color:#9295a3;margin:0 0 6px}.tool-body pre{margin:0 0 14px}.tool-body pre:last-child{margin-bottom:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.75 ui-monospace,SFMono-Regular,Consolas,monospace;color:#74798a;max-height:340px;overflow:auto}.message{margin-bottom:12px}.message-summary{border:1px solid var(--line);border-radius:8px;padding:12px 14px;background:#fcfcfe;margin-bottom:12px}.message-summary summary{font-size:11px;color:#767b89;cursor:pointer}.message-summary pre{margin:10px 0 0;font-family:inherit;font-size:11px;line-height:1.9}.result-message{border:1px solid #e4eee8;background:#f8fcf9}.result-message summary{color:#5d8270}.role{font-size:10px;color:#7e789e;margin-bottom:5px}.technical-details{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}.technical-details>summary{font-size:11px;color:#989caa;cursor:pointer}.technical-details pre{margin-top:11px}.samples-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:15px}.samples-toolbar select,.samples-toolbar input{font-size:11px;padding:6px 8px;max-width:160px}.samples-toolbar input{max-width:180px}.samples-toolbar .search-field{flex:1;max-width:220px}.sample-list{display:flex;gap:8px;overflow-x:auto;margin-bottom:20px}.sample{padding:11px 13px;min-width:170px;max-width:240px;flex:0 0 auto;text-align:left;display:block;border-radius:8px;border:1px solid var(--line);background:white}.sample.active{border-color:#bdb7e6;background:#fbfaff}.sample strong{font-size:11px;font-weight:560;display:block;margin:0 0 5px}.sample small{font-size:10px;display:block}.sample .badge{font-size:9px;padding:2px 5px;margin-top:8px}.sample-inspector h3{font-size:14px;margin-bottom:8px}.sample-badges{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}.sample-approval{border:1px solid #eae8f3;background:#faf9fd;padding:11px 12px;border-radius:8px;display:flex;align-items:flex-start;font-size:11px;color:#827b97;margin:15px 0 18px}.sample-state{font-size:11px;color:#9498a4;line-height:1.8}.sample-id{font-size:10px;word-break:break-all;color:#a0a3ad}.task-detail.empty .detail-heading{display:none}.divider{border-top:1px solid var(--line);margin:22px 0}.footnote{font-size:10px;color:#a1a4ae;margin-top:14px}.status-line{font-size:10px;color:#a1a5b0;min-height:16px;display:flex;justify-content:space-between;gap:15px;margin-top:12px}#error{color:#b56464;font-size:12px;margin-top:10px}#error:empty{display:none}.drawer-backdrop{position:fixed;inset:0;background:rgba(31,32,47,.2);backdrop-filter:blur(2px);z-index:30}.drawer{position:fixed;right:0;top:0;bottom:0;width:min(580px,100%);z-index:31;background:white;box-shadow:-10px 0 60px #23203f12;overflow:auto;padding:28px 30px}.drawer-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}.drawer-header h2{font-size:18px}.drawer-close{border:0;background:#f4f4f8;border-radius:50%;width:28px;height:28px;padding:0;color:#9295a1;font-size:18px}.drawer-section{margin-bottom:27px}.drawer-section h3{font-size:13px;margin-bottom:11px}.drawer-section p{font-size:11px;color:#989ca9;margin-bottom:12px;line-height:1.8}.drawer-section label{font-size:11px;color:#767b89;display:block;line-height:1.8;margin:11px 0}.drawer-section label input:not([type=checkbox]),.drawer-section label select{display:block;width:100%;margin-top:6px;font-size:12px}.drawer-section details{border-top:1px solid var(--line);margin-top:13px;padding-top:13px}.drawer-section summary{font-size:11px;color:#8d92a0;cursor:pointer}.drawer-section pre{margin:10px 0}.export-projects{max-height:270px;overflow:auto;border:1px solid var(--line);padding:4px 12px;border-radius:8px;margin-bottom:15px}.export-project-label{display:flex!important;gap:4px;align-items:flex-start}.export-project-label small{display:block;font-size:10px}.export-project-label span{flex:1}.drawer .primary{width:100%;padding:11px}#export-status{font-size:11px;color:#847b9e;margin-top:12px}.table{overflow:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;border-bottom:1px solid var(--line);padding:10px 6px;font-size:10px;vertical-align:top;word-break:break-all}th{font-weight:500;color:#8e93a0}td{color:#979ca7}.filter-row{display:flex;gap:10px}.filter-row label{flex:1;min-width:0}.exclusion-row{display:flex;justify-content:space-between;gap:16px;font-size:11px;padding:8px 0;color:#8e94a1;border-bottom:1px solid #f0f1f5}.reason-label{overflow-wrap:anywhere}[hidden]{display:none!important}
@media(min-width:1600px){.workspace{grid-template-columns:330px minmax(0,1fr)}.role-card{padding:17px}.role-name{font-size:13px}.main{padding-top:38px}.detail-heading{padding:30px 34px 23px}.tabs{padding:0 34px}.tab-pane{padding:28px 34px}}
@media(max-width:1180px){.sidebar{width:170px;padding:26px 12px}.shell{margin-left:170px}.main{padding:25px 22px}.topbar{padding:0 22px}.workspace{grid-template-columns:255px minmax(0,1fr)}.detail-heading{padding:23px 21px 17px}.tabs{padding:0 21px}.tab-pane{padding:22px 21px}.role-card{min-width:140px}.metric{padding:0 19px}}
@media(max-width:900px){.sidebar{width:67px;padding:24px 10px}.brand{margin:0 auto 36px}.brand-text,.nav-caption,.nav-link .nav-text,.operator div:not(.avatar){display:none}.nav-link{justify-content:center;padding:11px}.operator{margin:20px 5px 0}.shell{margin-left:67px}.workspace{grid-template-columns:230px minmax(0,1fr)}.main{padding:25px 18px}.topbar{padding:0 18px}.page-heading h1{font-size:22px}.metric{padding:0 14px}.metric strong{font-size:25px}.metric small{font-size:9px}.detail-heading h2{font-size:18px}.samples-toolbar{flex-wrap:wrap}}
@media(max-width:700px){.sidebar{display:none}.shell{margin-left:0}.topbar{height:55px;padding:0 18px}.connection{display:none}.main{padding:22px 14px 35px}.page-heading{align-items:flex-start;margin-bottom:20px}.page-heading p{font-size:11px;max-width:170px}.page-heading .actions button{font-size:11px;padding:7px 9px}.page-heading .actions .optional-label{display:none}.metrics{padding:16px 0;margin-bottom:21px;grid-template-columns:repeat(2,1fr);gap:18px 0}.metric{padding:0 18px}.metric:nth-child(2){border:0}.metric strong{font-size:24px;margin:5px 0 1px}.metric-label{font-size:10px}.workspace{display:block;min-height:0}#projects{max-height:800px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#dedde8 transparent}.task-library{border-right:0;border-bottom:1px solid var(--line)}.search-wrap{padding:14px 14px 9px}.list-toolbar{padding:0 15px 10px}#projects{display:flex;overflow-x:auto;overflow-y:hidden;max-height:none;border-top:1px solid var(--line);padding:0 6px;gap:0}.project{border:0;border-right:1px solid #efeff4;min-width:215px;max-width:215px}.project.active:before{top:auto;bottom:0;height:2px;left:14px;right:14px;width:auto}.project-open{padding:14px 12px}.project-source{display:none}.project-title{font-size:12px;min-height:38px}.paging{padding:8px 14px}.detail-heading{padding:22px 18px 16px}.tabs{padding:0 18px;gap:24px}.tab-pane{padding:22px 18px}.role-card{min-width:144px}.flow-note:after{content:" · 左右滑动可查看全部角色"}.section-head small{max-width:140px;text-align:right}.drawer{padding:24px 20px}.status-line{font-size:9px}.top-actions .quiet-button{font-size:10px}}
</style></head><body>
<aside class="sidebar"><a href="/admin" class="brand" aria-label="Argus 运营后台"><span class="brand-mark">A</span><span class="brand-text">ARGUS</span></a>
<div class="nav-caption">工作空间</div><a class="nav-link" href="/admin"><span class="icon"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg></span><span class="nav-text">运营概览</span></a>
<a class="nav-link active" href="/admin/data" aria-current="page"><span class="icon"><svg viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></svg></span><span class="nav-text">数据工作台</span></a>
<button class="nav-link" id="nav-audit"><span class="icon"><svg viewBox="0 0 24 24"><path d="M8 3h8l4 4v14H4V3h4zm8 0v5h4M8 12h8m-8 4h6"/></svg></span><span class="nav-text">导出与审计</span></button>
<div class="sidebar-bottom"><a class="nav-link" href="/invite"><span class="icon"><svg viewBox="0 0 24 24"><path d="M14 4h6v6m0-6L10 14m-3-8H4v14h14v-3"/></svg></span><span class="nav-text">进入用户端</span></a><div class="operator"><div class="avatar">AD</div><div>团队管理员<small id="session-label">仅数据后台</small></div></div></div></aside>
<div class="shell"><header class="topbar"><div class="breadcrumb"><span>工作空间</span><span>/</span><strong>数据工作台</strong></div><div class="top-actions"><span class="connection" id="connection"><span class="status-dot"></span><span id="connection-label">连接中</span></span><button class="quiet-button" id="open-diagnostics">采集诊断</button></div></header>
<main class="main"><div class="page-heading"><div><h1>数据工作台</h1><p>看见四个角色的真实过程，先采集，再验收。</p></div><div class="actions"><button class="button-icon" id="reload"><span class="icon"><svg viewBox="0 0 24 24"><path d="M20 7v5h-5M4 17v-5h5M6.2 7a7 7 0 0 1 11.6-1L20 9M4 15l2.2 3A7 7 0 0 0 18 17"/></svg></span><span class="optional-label">刷新</span></button><button class="primary button-icon" id="open-export"><span class="icon"><svg viewBox="0 0 24 24"><path d="M12 3v12m-4-4 4 4 4-4M5 16v5h14v-5"/></svg></span>导出全部过程</button></div></div>
<div id="metrics" class="metrics" aria-label="本页数据概览"></div><div class="library-heading"><h2>任务库</h2><span id="overview-scope" class="scope-label">按真实任务记录归集</span></div>
<p id="overview-limits" class="subtle-copy" style="margin:-3px 0 13px" hidden></p><div class="workspace"><aside class="task-library"><div class="search-wrap"><label class="search-field"><span class="icon"><svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/></svg></span><input id="project-query" placeholder="搜索本页任务" aria-label="搜索本页任务" maxlength="160"></label></div><div class="list-toolbar"><span id="project-count">任务记录</span><select id="task-view" aria-label="任务显示范围"><option value="all">全部任务</option><option value="collected">已采集过程</option><option value="ready">已有验收记录</option><option value="activity">项目活动</option></select></div><div id="projects"></div><div class="paging"><button id="prev" disabled>← 上一页</button><span id="page"></span><button id="next" disabled>下一页 →</button></div></aside>
<section class="task-detail" id="task-detail"><div class="detail-heading"><div class="detail-topline"><span class="detail-kicker" id="task-category">任务详情</span><span id="task-outcome"></span></div><h2 id="task-title">正在读取任务…</h2><div class="task-meta" id="task-meta"></div><p id="task-description" class="goal-preview"></p><details class="task-goal"><summary>查看完整任务与来源</summary><pre id="task-goal"></pre></details><label class="observation-scope-label">过程范围 <select id="observation-scope"><option value="project">整个项目（含任务前规划）</option><option value="task">仅当前任务</option></select></label></div>
<div class="tabs" role="tablist" aria-label="任务详情"><button id="tab-collaboration" class="tab active" role="tab" aria-selected="true" aria-controls="pane-collaboration">任务协作</button><button id="tab-samples" class="tab" role="tab" aria-selected="false" aria-controls="pane-samples">全部过程 <span id="tab-sample-count" class="tab-count">0</span></button></div>
<div id="pane-collaboration" class="tab-pane" role="tabpanel" aria-labelledby="tab-collaboration"><div class="section-head"><h3 id="roles-heading">整个项目的角色过程</h3><small id="collaboration-caption">依据实际运行记录</small></div><p class="subtle-copy" style="margin:-6px 0 16px">点选角色查看真实记录；项目级过程保留原有归属。</p><div id="collaboration-flow" class="flow"></div><div id="handoffs" class="handoffs"></div><p id="flow-note" class="flow-note"></p><div id="activity-timeline" class="activity-timeline"></div><div id="capture-note"></div><section class="collaboration-steps"><div class="role-run-heading"><h3 id="role-run-title">执行过程</h3><select id="role-run" class="run-picker" aria-label="选择运行记录" hidden></select></div><p id="role-run-description" class="subtle-copy" style="margin-bottom:14px"></p><div id="role-messages" class="timeline"></div><button id="role-load-more" class="quiet-button" hidden>加载后续过程</button><details id="role-diagnostics" class="technical-details" hidden><summary>查看此角色的采集记录</summary><pre id="role-source"></pre></details></section></div>
<div id="pane-samples" class="tab-pane" role="tabpanel" aria-labelledby="tab-samples" hidden><div class="section-head"><h3>已采集的全部过程</h3><small id="sample-count"></small></div><p id="sample-preview-status" class="subtle-copy" role="status" style="margin-bottom:12px"></p><div class="samples-toolbar"><label class="search-field"><input id="task-query" placeholder="按任务编号筛选" aria-label="筛选任务编号"></label><select id="sample-kind" aria-label="样本类型"><option value="all">全部过程</option><option value="tools">有工具调用</option><option value="chat">纯模型对话</option></select></div><div id="samples" class="sample-list"></div><button id="load-more-observations" class="quiet-button" hidden>加载后续过程</button><div class="sample-inspector"><h3 id="sample-title">选择样本查看</h3><div id="sample-badges" class="sample-badges"></div><p id="sample-state" class="sample-state"></p><div id="sample-selection" style="margin-top:15px"><button id="select-sample-project" class="quiet-button" style="padding:0;color:var(--accent)">将此项目加入导出范围</button><span id="sample-selection-status" class="subtle-copy"></span></div><div id="messages" class="timeline"></div><details class="technical-details"><summary>原始记录与来源</summary><p id="sample-identity" class="sample-id"></p><h4 class="role" style="margin-top:14px">记录中的工具定义</h4><pre id="tools"></pre><h4 class="role" style="margin-top:14px">来源与质量依据</h4><pre id="source"></pre></details><details class="technical-details"><summary>模型消息结构预览</summary><label class="muted" style="display:block;font-size:11px;margin:12px 0">格式 <select id="format"><option value="context">记录中的模型消息</option><option value="episode">完整原始过程</option></select></label><pre id="formats"></pre><p class="footnote">这里展示采集时的原始结构，质量验收状态单独记录。</p></details></div></div></section></div>
<div class="status-line"><span id="load-status" role="status">正在读取服务器数据…</span><span>Argus · 团队内测</span></div><p id="error" role="alert"></p></main></div>
<div id="drawer-backdrop" class="drawer-backdrop" hidden></div><aside id="drawer" class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" hidden><div class="drawer-header"><h2 id="drawer-title">导出数据</h2><button id="close-drawer" class="drawer-close" aria-label="关闭面板">×</button></div>
<div id="drawer-export"><section class="drawer-section"><h3>选择项目</h3><p>导出所选项目中已保留的所有角色过程，包含无工具调用、未结束与失败的记录。</p><div class="filter-row"><label>导出用途<select id="purpose"><option value="internal_training">内部训练</option><option value="external_sharing">第三方 / 商业分享</option></select></label><label>邀请码账号<input id="tenant" placeholder="全部账号" maxlength="160"></label></div><div id="export-projects" class="export-projects"></div><p id="review-summary">选择需要导出的项目。</p><button id="download" class="primary" disabled>下载全部过程 ZIP</button><p id="export-status" role="status"></p><p style="margin-top:14px">包含原始 JSONL 过程记录与采集状态。质量验收信息单独保留，不会因为尚未验收而省略过程。</p></section></div>
<div id="drawer-diagnostics" hidden><section class="drawer-section"><h3>采集状态与范围</h3><pre id="collector"></pre><details><summary>采集与格式限制</summary><pre id="limits"></pre></details></section><section class="drawer-section"><h3>采集缺口与排除记录</h3><p>采集状态与质量验收分别记录；未完成的过程也可查看。</p><div id="reasons"></div><details><summary>完整诊断索引</summary><pre id="diagnostics"></pre></details></section></div>
<div id="drawer-audit" hidden><section class="drawer-section"><h3>最近导出记录</h3><p>保留每次导出的时间、范围与操作结果。</p><div class="table"><table><thead><tr><th>时间 / 操作</th><th>操作身份</th><th>结果</th><th>导出范围</th></tr></thead><tbody id="audit"></tbody></table></div></section></div></aside>
</body></html>"""

SCRIPT = r"""
'use strict';
const el=id=>document.getElementById(id);
const entryQuery=new URLSearchParams(window.location?.search||'');
const entrySid=entryQuery.get('sid');
let preview=null, overview=null, fallbackTasks=null, active=null, activeProject=null, activeTask=null, collaboration=null, activeRole=null;
let version=0, collaborationVersion=0, sampleVersion=0, readonly=true, busy=false, currentTab='collaboration', sampleLoadError='', sampleLoading=false;
let observationCursor=null, observationHasMore=false, observationScope='project';
const selected=new Set();
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
const visibleObservations=()=>observationScope==='project'?candidatesFor(activeProject):taskCandidates(activeTask);
const observationAssociation=episode=>!episode.task_id?'项目级过程 · 未关联具体任务':episode.task_id===activeTask?.task_id?'当前任务过程':'项目内任务 '+episode.task_id;
const taskKey=task=>task.id||JSON.stringify([task.tenant_id,task.sid,task.task_id||null]);
const taskName=task=>cleanTitle(task.mission_title||task.title)||projectName(task);
const taskDescription=task=>task.request?.text||task.objective||'该轮原始请求未记录，可查看已保留的角色活动与执行过程。';
function tasksOnPage(){
  if(Array.isArray(overview?.tasks))return overview.tasks;
  if(Array.isArray(fallbackTasks))return fallbackTasks;
  return (preview?.projects||[]).flatMap(project=>{const candidates=candidatesFor(project),ids=[...new Set(candidates.map(row=>row.task_id||null))];
    return(ids.length?ids:[null]).map(task_id=>({...project,task_id,id:JSON.stringify([project.tenant_id,project.sid,task_id]),title:projectName(project),roles:[],quality:{approved_samples:candidates.filter(row=>row.task_id===task_id&&row.quality_approved).length},task_outcome:{state:'unknown',label:'任务结果未确认'}}));});
}
const toolCount=candidate=>candidate?.episode?(candidate.episode.events||[]).filter(event=>event.kind==='tool_call').length:(candidate?.sample?.messages||[]).reduce((count,message)=>count+(message.tool_calls?.length||0),0);
const episodeKey=episode=>String(episode.episode_id);
const hasRetainedEvents=episode=>episode.collection?Number(episode.collection.event_count||episode.events?.length||0)>0:episode.raw_available===true||Number(episode.observed_event_count||0)>0;
const collectionLabel=episode=>episode.runtime?.recovery?'历史会话已恢复':episode.collection?.complete?'已采集 · 已结束':episode.state==='capturing'?'采集中':(episode.events||[]).length?'已采集 · 未完整结束':'尚无事件内容';
const qualityLabel=episode=>({approved:'质量已验收',candidate:'质量待验收',needs_work:'质量待改进',not_evaluated:'尚未验收'}[episode.quality?.state]||'尚未验收');
const taskCollected=task=>task.collection?.retained_episodes??task.collection?.collected_episodes??Object.values(task.collection?.states||{}).reduce((a,b)=>a+Number(b||0),0);
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
function selection(){return preview?preview.projects.filter(project=>project.eligible===true&&selected.has(key(project))):[];}
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
  if(kind==='export'&&!selected.size&&activeProject?.eligible===true){selected.add(key(activeProject));renderProjects();}
  el('drawer-title').textContent={export:'导出全部过程',diagnostics:'采集诊断',audit:'导出与审计'}[kind];
  for(const name of ['export','diagnostics','audit'])el('drawer-'+name).hidden=name!==kind;
  el('drawer').hidden=false;el('drawer-backdrop').hidden=false;
  el('close-drawer').focus?.();
}
function closeDrawer(){el('drawer').hidden=true;el('drawer-backdrop').hidden=true;drawerFocus?.focus?.();}
function updateControls(){
  const projects=selection();
  el('download').disabled=readonly||busy||!projects.length;
  const included=Boolean(active&&selected.has(key(active)));
  el('select-sample-project').hidden=!active||included;
  el('select-sample-project').disabled=readonly||busy||!active||!preview?.projects.some(project=>key(project)===key(active)&&project.eligible===true);
  el('sample-selection-status').textContent=included?'此项目已加入导出范围':'';
  el('review-summary').textContent=number(projects.length)+' 个项目 · 导出全部已保留的角色过程'+(readonly?' · 当前为只读会话':'');
  el('prev').disabled=busy||!preview||!preview.offset;el('next').disabled=busy||!preview?.has_more_projects;
  for(const id of ['purpose','tenant','project-query','reload'])el(id).disabled=busy;
  for(const input of document.querySelectorAll('.project-choice'))input.disabled=readonly||busy||input.dataset.eligible!=='true';
  for(const id of ['load-more-observations','role-load-more']){el(id).hidden=!observationHasMore;el(id).disabled=sampleLoading;}
}
function renderFormat(){
  el('formats').textContent=active?json(el('format').value==='episode'?active.episode:active.sample):'';
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
function messageText(messages){
  return (messages||[]).map(message=>{
    const role={system:'系统',developer:'开发者',user:'用户',assistant:'模型',toolResult:'工具返回',tool:'工具返回'}[message.role]||message.role||'消息';
    const content=typeof message.content==='string'?message.content:Array.isArray(message.content)?message.content.map(block=>block.type==='text'?block.text:json(block)).join('\n'):json(message.content);
    return role+'\n'+(content||'');
  }).join('\n\n');
}
function observationCandidate(episode,task){
  const context=(episode.events||[]).find(event=>['context','provider_request'].includes(event.kind))?.payload||{};
  return {tenant_id:task.tenant_id,sid:task.sid,task_id:episode.task_id??null,event_id:'episode-'+episode.episode_id,
    episode,quality_approved:episode.quality?.state==='approved',sample:{messages:context.messages||[],tools:context.tools||[]}};
}
function renderObservation(target,episode){
  target.replaceChildren();const events=episode?.events||[];
  if(episode)target.append(textNode('p',observationAssociation(episode),'subtle-copy'));
  if(episode?.runtime?.recovery)target.append(textNode('p','从原始会话日志恢复；原始模型请求和工具定义未保留。','retained-notice'));
  if(!events.length){target.append(textNode('div','此段过程已建立记录，目前没有保留的事件内容。','empty-state'));return;}
  const results=new Map(events.filter(event=>event.kind==='tool_result').map(event=>[event.payload?.toolCallId,event]));
  const calls=new Set(events.filter(event=>event.kind==='tool_call').map(event=>event.payload?.toolCallId));
  let step=0,section=target;const totalCalls=events.filter(event=>event.kind==='tool_call').length;
  for(const event of events){
    const payload=event.payload||{};
    if(event.kind==='tool_result'&&calls.has(payload.toolCallId))continue;
    if(event.kind==='tool_call'){
      step+=1;if(step===7){const more=document.createElement('details');more.className='more-steps';const rest=document.createElement('div');rest.className='timeline';more.append(textNode('summary','展开其余 '+number(totalCalls-6)+' 次工具调用'),rest);target.append(more);section=rest;}
      const result=results.get(payload.toolCallId),call={function:{name:payload.toolName,arguments:payload.input}},summary=toolSummary(call);
      const item=document.createElement('details');item.className='tool-step';const heading=document.createElement('summary'),copy=document.createElement('div');copy.className='step-heading';
      copy.append(textNode('strong',summary.label),textNode('small',summary.description||payload.toolName||'工具调用'));
      heading.append(textNode('span',String(step).padStart(2,'0'),'step-number'),copy,textNode('span',result?(result.payload?.isError?'!':'✓'):'…','step-check'),textNode('span','›','step-arrow'));
      const body=document.createElement('div');body.className='tool-body';body.append(textNode('h4','真实调用参数'),textNode('pre',json(payload.input)));
      body.append(textNode('h4',result?(result.payload?.isError?'工具报告错误':'真实返回内容'):'尚未保留对应返回'),textNode('pre',result?json(result.payload?.content):'调用记录已保留，当前没有对应的返回记录。'));
      body.append(textNode('small','调用编号：'+payload.toolCallId));item.append(heading,body);section.append(item);continue;
    }
    const labels={session_message:'历史会话消息',context:'模型输入',provider_request:'模型请求',agent_end:'模型输出',tool_result:'工具返回（未匹配到调用）',settled:'本段过程结束',quarantine:'过程状态记录'};
    const title=labels[event.kind]||'过程事件 · '+event.kind;
    const body=Array.isArray(payload.messages)?messageText(payload.messages):json(payload);
    const item=detailsBlock(title,body,'message-summary'+(event.kind==='agent_end'?' result-message':''));
    if(event.kind==='agent_end'&&!totalCalls)item.open=true;
    (event.kind==='agent_end'?target:section).append(item);
  }
}
function inspect(candidate){
  active=candidate;const episode=candidate?.episode;
  el('sample-title').textContent=episode?(episode.label||roleLabel(episode.role))+' · 过程 '+episode.episode_id:'选择过程查看';
  el('sample-identity').textContent=candidate?candidate.sid+' · '+candidate.tenant_id+' · 任务 '+(candidate.task_id||'未关联') :'';
  el('sample-badges').replaceChildren();for(const id of ['tools','source','sample-state'])el(id).textContent='';
  if(episode){
    el('sample-badges').append(badge(observationAssociation(episode)),badge(collectionLabel(episode),'purple'),badge(qualityLabel(episode),episode.quality?.state==='approved'?'ready':''),badge(episode.runtime?.recovery?'观察器工具轨迹未保留':number(toolCount(candidate))+' 次工具调用'));
    el('sample-state').textContent=number(episode.collection?.event_count??episode.events?.length)+' 条真实事件'+(episode.collection?.issues?.length?' · 过程状态信息见来源详情':'');
    el('tools').textContent=json(candidate.sample.tools||[]);el('source').textContent=json(episode);renderObservation(el('messages'),episode);
  }else el('messages').replaceChildren();
  renderFormat();updateControls();
}
function renderSamples(){
  el('samples').replaceChildren();const query=el('task-query').value.trim(),kind=el('sample-kind').value||'all';
  const candidates=visibleObservations().filter(candidate=>(!query||String(candidate.task_id||'').includes(query))&&(kind==='all'||(kind==='tools')===(toolCount(candidate)>0)));
  el('sample-count').textContent=sampleLoading?'正在读取…':number(candidates.length)+' 段过程';el('tab-sample-count').textContent=sampleLoading?'…':number(visibleObservations().length);
  const displayActive=active?.event_id||candidates[0]?.event_id;
  candidates.forEach(candidate=>{
    const episode=candidate.episode,button=document.createElement('button');button.className='sample'+(displayActive===candidate.event_id?' active':'');
    button.append(textNode('strong',(episode.label||roleLabel(episode.role))+' · '+collectionLabel(episode)),textNode('small',observationAssociation(episode)+' · '+number(toolCount(candidate))+' 次工具调用'),badge(qualityLabel(episode),episode.quality?.state==='approved'?'ready':''));
    button.onclick=()=>{inspect(candidate);renderSamples();};el('samples').append(button);
  });
  if(active&&!candidates.some(candidate=>candidate.event_id===active.event_id))inspect(null);
  if(!active&&candidates.length)inspect(candidates[0]);
  if(!candidates.length){el('samples').append(textNode('div',sampleLoading?'正在读取真实过程…':sampleLoadError||'这次任务目前没有保留的过程。','empty-state'));inspect(null);}
}
function projectCandidates(project){return candidatesFor(project);}
function renderProjects(){
  el('projects').replaceChildren();el('export-projects').replaceChildren();
  const mode=el('task-view').value||'all',query=el('project-query').value.trim().toLowerCase();
  const allTasks=tasksOnPage(),tasks=[...allTasks].filter(task=>mode==='activity'?!task.task_id:Boolean(task.task_id)).sort((a,b)=>(b.last_observed_at||0)-(a.last_observed_at||0));
  for(const project of preview?.projects||[]){
    const candidates=candidatesFor(project),ready=candidates.filter(candidate=>candidate.quality_approved).length;
    const input=document.createElement('input');input.type='checkbox';input.className='project-choice';input.dataset.eligible=String(project.eligible===true);input.checked=selected.has(key(project));
    input.setAttribute('aria-label','选择 '+project.tenant_id+' / '+project.sid);
    input.onchange=()=>{if(input.checked)selected.add(key(project));else selected.delete(key(project));
      updateControls();};
    const label=document.createElement('label');label.className='export-project-label';const copy=textNode('span',projectName(project));
    copy.append(textNode('small',project.eligible?'已授权 · 导出此项目全部过程':project.reason||'用途未授权'));label.append(input,copy);el('export-projects').append(label);
  }
  let shown=0;
  for(const task of tasks){
    const candidates=taskCandidates(task),ready=task.quality?.approved_samples??candidates.filter(row=>row.quality_approved).length;
    const search=[taskName(task),task.title,task.request?.text,task.task_id,task.sid,task.tenant_id].filter(Boolean).join(' ').toLowerCase();
    if((mode==='ready'&&!ready)||(mode==='collected'&&!taskCollected(task))||(query&&!search.includes(query)))continue;
    shown+=1;const item=document.createElement('article');item.className='project'+(activeTask&&taskKey(activeTask)===taskKey(task)?' active':'');
    const button=document.createElement('button');button.className='project-open';button.setAttribute('aria-label','查看任务 '+taskName(task));
    button.append(textNode('div',task.task_id?taskName(task):taskName(task)+' · 项目活动','project-title'));const meta=document.createElement('div');meta.className='project-meta';
    const retained=taskCollected(task);meta.append(badge(retained?'已采集 '+number(retained)+' 段':'运行观察记录',retained?'purple':''),textNode('small',task.tenant_id));
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
  const values=[['真实任务',counts?.tasks??tasksOnPage().filter(task=>task.task_id).length,'本页具有明确关联的任务'],['已采集过程',counts?.observed_episodes??counts?.collected_episodes??tasksOnPage().reduce((sum,task)=>sum+taskCollected(task),0),'包含四角色、未结束与失败的记录'],['真实工具调用',counts?.tool_pairs??calls,'本页已配对的调用与返回'],['质量已验收',counts?.approved_samples??data.counts.sft,'验收状态与是否采集分别记录']];
  for(const[label,value,caption]of values){const box=document.createElement('div');box.className='metric';box.append(textNode('div',label,'metric-label'),textNode('strong',number(value)),textNode('small',caption));el('metrics').append(box);}
  el('overview-scope').textContent='本页 '+number(data.projects.length)+' 个项目 · 最近活动优先';
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
  sampleLoadError='';sampleLoading=true;observationCursor=null;observationHasMore=false;preview.candidates=[];sampleVersion+=1;inspect(null);renderProjects();renderSamples();renderTaskHeader();
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
  el('task-meta').replaceChildren(textNode('span',task.tenant_id),textNode('span','·','separator'),textNode('span',number(roles)+' 个协作角色'),textNode('span','·','separator'),textNode('span',number(calls)+' 次工具调用'),textNode('span','·','separator'),textNode('span',number(ready)+' 条质量已验收'));
  el('task-outcome').append(outcomeTag(task.task_outcome));
  el('task-goal').textContent=json({title:taskName(task),tenant_id:task.tenant_id,project:task.sid,task_id:task.task_id,request:task.request||null,objective:task.objective||null,mission_brief_source:task.mission_brief_source||null,request_truncated:task.request?.truncated||false,task_outcome:task.task_outcome,quality:task.quality,global_complete:task.global_complete??false});
}
function renderAudit(data){
  el('audit').replaceChildren();
  for(const event of data.events||[]){const row=document.createElement('tr');
    const reviewName=event.actor||'管理员';
    for(const value of [new Date(event.created_at*1000).toLocaleString()+' · '+event.action,reviewName,event.outcome,event.action?.includes('observations')?'全部保留过程':number(event.sft)+' 条训练样本'])row.append(textNode('td',value));el('audit').append(row);}
  if(!(data.events||[]).length){const row=document.createElement('tr'),cell=textNode('td','暂无导出记录');cell.setAttribute('colspan','4');row.append(cell);el('audit').append(row);}
}
function renderProjectDiagnostics(data){
  el('limits').textContent=json(data.limits||[]);el('reasons').replaceChildren();
  for(const[reason,count]of Object.entries(data.reason_counts||{})){const row=document.createElement('div');row.className='exclusion-row';row.append(textNode('span',reason,'reason-label'),textNode('strong',number(count)));el('reasons').append(row);}
  if(!Object.keys(data.reason_counts||{}).length)el('reasons').append(textNode('p','当前项目没有排除记录。','muted'));
  el('diagnostics').textContent=json({project:activeTask?{tenant_id:activeTask.tenant_id,sid:activeTask.sid}:null,records:data.diagnostics||[],total:data.diagnostics_total,truncated:data.diagnostics_truncated});
}
async function loadProjectPreview(task,cursor=null){
  const current=++sampleVersion,taskIdentity=taskKey(task);
  el('sample-preview-status').textContent='正在读取此任务的全部过程…';sampleLoadError='';sampleLoading=true;
  try{
    const params=new URLSearchParams({purpose:el('purpose').value||'internal_training'});if(observationScope==='task'&&task.task_id)params.set('task_id',task.task_id);if(cursor)params.set('cursor',cursor);
    const result=await api('/admin/api/training/observations/'+encodeURIComponent(task.tenant_id)+'/'+encodeURIComponent(task.sid)+'?'+params);
    if(current!==sampleVersion||!activeTask||taskKey(activeTask)!==taskIdentity)return;
    if(!Array.isArray(result.episodes)||result.tenant_id!==task.tenant_id||result.sid!==task.sid)throw Error('过程数据格式不正确');
    const byId=new Map(cursor?visibleObservations().map(candidate=>[episodeKey(candidate.episode),candidate.episode]):[]);
    for(const episode of result.episodes.filter(row=>observationScope==='project'||(row.task_id||null)===(task.task_id||null))){
      const previous=byId.get(episodeKey(episode)),events=new Map((previous?.events||[]).map(event=>[event.id||String(event.sequence),event]));
      for(const event of episode.events||[])events.set(event.id||String(event.sequence),event);
      byId.set(episodeKey(episode),{...previous,...episode,events:[...events.values()].sort((a,b)=>(a.sequence||0)-(b.sequence||0))});
    }
    const episodes=[...byId.values()];preview.candidates=episodes.map(episode=>observationCandidate(episode,task));sampleLoading=false;
    observationHasMore=result.pagination?.has_more===true;observationCursor=result.pagination?.next_cursor||null;
    renderProjects();renderSamples();renderProjectDiagnostics(result);
    el('sample-preview-status').textContent='已加载 '+number(episodes.length)+' 段过程'+(observationHasMore?' · 还有后续记录':' · 当前范围的保留记录已加载');
    if(collaboration)renderCollaboration();updateControls();
  }catch(error){
    if(current!==sampleVersion||!activeTask||taskKey(activeTask)!==taskIdentity)return;
    sampleLoading=false;sampleLoadError='当前过程暂不可读取：'+error.message;if(!cursor)preview.candidates=[];
    el('sample-preview-status').textContent=sampleLoadError;renderSamples();if(collaboration)renderCollaboration();updateControls();
  }
}
async function load(offset=0){
  if(busy)return;
  const previousKey=activeTask?taskKey(activeTask):null,current=++version;collaborationVersion+=1;sampleVersion+=1;
  selected.clear();preview=null;overview=null;fallbackTasks=null;activeProject=null;activeTask=null;collaboration=null;sampleLoadError='';sampleLoading=false;observationCursor=null;observationHasMore=false;inspect(null);
  el('sample-preview-status').textContent='';el('load-status').textContent='正在读取协作概览…';el('error').textContent='';el('connection-label').textContent='连接中';el('connection').dataset.state='loading';
  const params=new URLSearchParams({purpose:el('purpose').value||'internal_training',offset:String(offset)});
  if(el('tenant').value.trim())params.set('tenant',el('tenant').value.trim());
  try{
    const[taskData,identity,collector,audit]=await Promise.all([api('/admin/api/training/collaboration?'+params),api('/admin/status'),
      api('/admin/api/research/status').catch(error=>({state:'unavailable',detail:error.message})),api('/admin/api/training/audit').catch(error=>({events:[],error:error.message}))]);
    if(current!==version)return;
    readonly=identity.role!=='admin'||identity.readonly!==false;overview=taskData;
    if(Array.isArray(taskData.tasks)&&Array.isArray(taskData.projects)){
      preview={projects:taskData.projects.map(project=>({...project,preview_loaded:false})),candidates:[],counts:{},
        offset:taskData.offset||0,selection_limit:taskData.selection_limit||20,total_projects:taskData.total_projects??taskData.projects.length,
        has_more_projects:taskData.has_more_projects===true,next_offset:taskData.next_offset};
    }else throw Error('协作概览格式不正确');
    el('session-label').textContent=readonly?'只读会话':'仅数据后台';metrics();renderProjects();
    el('page').textContent='第 '+number(Math.floor(preview.offset/preview.selection_limit)+1)+' 页';
    el('collector').textContent=json({research_journal:collector,collaboration_limitations:taskData.limitations||null,completeness:taskData.completeness||null});renderProjectDiagnostics({});renderAudit(audit);
    if(audit.error)el('error').textContent='审计读取失败：'+audit.error;
    const tasks=tasksOnPage(),task=tasks.find(row=>taskKey(row)===previousKey)||tasks.find(row=>entrySid&&row.sid===entrySid&&row.task_id)||tasks.find(row=>entrySid&&row.sid===entrySid)||tasks.find(row=>row.task_id)||tasks[0];
    el('load-status').textContent='协作概览已同步 · 全部过程按任务读取';el('connection-label').textContent='已连接';el('connection').dataset.state='ready';
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
  const observed=new Map((collaboration?.roles||[]).map(role=>[role.role,role]));
  for(const candidate of visibleObservations())if(candidate.episode?.role&&!observed.has(candidate.episode.role))observed.set(candidate.episode.role,{role:candidate.episode.role,label:candidate.episode.label,observations:0});
  return ['manager','planner','engineer','reviewer'].map(name=>observed.get(name)||{role:name,label:roleLabel(name),observations:0,episodes:0,tool_pairs:0,unobserved:true});
}
function allRoleEpisodes(){
  const combined=new Map((collaboration?.episodes||[]).map(episode=>[episodeKey(episode),episode]));
  for(const candidate of visibleObservations())combined.set(episodeKey(candidate.episode),candidate.episode);
  return [...combined.values()];
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
  const roles=collaborationRoles(),episodes=allRoleEpisodes();
  el('collaboration-flow').replaceChildren();el('handoffs').replaceChildren();el('capture-note').replaceChildren();el('activity-timeline').replaceChildren();
  el('flow-note').textContent='四个角色的采集情况分别展示；下方时间线来自实际保留的记录。';el('role-source').textContent='';el('role-diagnostics').hidden=true;
  const observed=roles.filter(role=>!role.unobserved).length;el('collaboration-caption').textContent=(observationScope==='project'?'项目范围 · ':'当前任务 · ')+number(observed)+' / 4 个角色已有记录';
  el('roles-heading').textContent=observationScope==='project'?'整个项目的角色过程':'当前任务的角色过程';
  if(!activeTask){el('role-messages').replaceChildren();return;}
  if(!activeRole||!roles.some(role=>role.role===activeRole))activeRole=roles.find(role=>!role.unobserved)?.role||roles[0].role;
  roles.forEach(role=>{
    const ownEpisodes=episodes.filter(episode=>episode.role===role.role),retained=ownEpisodes.filter(hasRetainedEvents).length,raw=visibleObservations().filter(candidate=>candidate.episode.role===role.role);
    const button=document.createElement('button');button.className='role-card'+(activeRole===role.role?' active':'');
    const top=document.createElement('div');top.className='role-card-top';const copy=document.createElement('div');copy.append(textNode('div',role.label||roleLabel(role.role),'role-name'),textNode('div',role.role,'role-caption'));
    top.append(textNode('span',({manager:'统',planner:'规',engineer:'执',reviewer:'审'})[role.role],'role-symbol'),copy);button.append(top,textNode('div',roleDescriptions[role.role],'role-caption'));
    button.append(badge(retained?'已采集 '+number(retained)+' 段':role.unobserved?'未采到记录':'已有活动记录',retained?'purple':''));
    const meta=document.createElement('div');meta.className='role-card-meta';
    if(raw.length){const calls=raw.reduce((sum,candidate)=>sum+toolCount(candidate),0);meta.append(textNode('span',raw.every(candidate=>candidate.episode.runtime?.recovery)?'历史会话消息已恢复':calls?number(calls)+' 次已加载调用':raw.some(candidate=>(candidate.episode.events||[]).length)?'模型输入与输出已保留':'等待后续事件'));}
    else meta.append(textNode('span',retained?'过程正文按页加载':role.unobserved?'尚无保留的角色过程':number(role.observations)+' 条活动观察'));
    button.append(meta);const stamp=firstRoleObservation(role.role);if(Number.isFinite(stamp))button.append(textNode('div',new Date(stamp*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})+' 首条记录','role-caption'));
    button.onclick=()=>{activeRole=role.role;renderCollaboration();};el('collaboration-flow').append(button);
  });
  for(const edge of collaboration?.handoffs||[]){const from=roles.find(role=>role.role===edge.from&&!role.unobserved),to=roles.find(role=>role.role===edge.to&&!role.unobserved);if(from&&to)el('handoffs').append(textNode('span',from.label+' → '+to.label+(edge.label?' · '+edge.label:''),'handoff'));}
  renderActivityTimeline(roles.filter(role=>!role.unobserved));
  if(collaboration?.error)el('capture-note').append(textNode('p','协作摘要暂不可读取：'+collaboration.error,'subtle-copy'));
  renderRole(roles.find(role=>role.role===activeRole)||roles[0]);updateControls();
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
  const label=role.label||roleLabel(role.role),episodes=allRoleEpisodes().filter(episode=>episode.role===role.role),candidates=visibleObservations().filter(candidate=>candidate.episode.role===role.role);
  const segments=(collaboration?.segments||[]).filter(segment=>segment.role===role.role&&segment.source_kind!=='tool_episode');
  el('role-run-title').textContent=label+' · 真实过程';el('role-run-description').textContent=(observationScope==='project'?'整个项目 · ':'当前任务 · ')+number(episodes.length)+' 段过程记录 · '+number(candidates.length)+' 段已加载正文';
  el('role-run').replaceChildren();el('role-run').hidden=candidates.length<2;
  candidates.forEach(candidate=>{const option=textNode('option','过程 '+candidate.episode.episode_id+' · '+observationAssociation(candidate.episode));option.value=candidate.event_id;el('role-run').append(option);});
  const chosen=candidates[0];
  if(sampleLoading&&!chosen)el('role-messages').replaceChildren(textNode('div','正在读取此角色的真实过程…','empty-state'));
  else if(chosen){el('role-run').value=chosen.event_id;renderObservation(el('role-messages'),chosen.episode);}
  else{
    el('role-messages').replaceChildren();
    if(sampleLoadError)el('role-messages').append(textNode('p',sampleLoadError,'retained-notice'));
    if(segments.length){el('role-messages').append(textNode('p','以下是已保留的活动摘要。','subtle-copy'));renderObservedSegments(el('role-messages'),segments.slice(0,15));}
    else el('role-messages').append(textNode('div',episodes.length?'该角色已有采集记录，请加载后续过程查看正文。':'尚未保留此角色的执行过程。','empty-state'));
  }
  el('role-run').onchange=()=>{const candidate=candidates.find(row=>row.event_id===el('role-run').value);if(candidate)renderObservation(el('role-messages'),candidate.episode);};
  el('role-source').textContent=json({role,episodes,segments,global_complete:collaboration?.global_complete});el('role-diagnostics').hidden=role.unobserved;
}
for(const id of ['task-query','sample-kind'])el(id).oninput=renderSamples;
el('select-sample-project').onclick=()=>{if(el('select-sample-project').disabled||!active)return;selected.add(key(active));renderProjects();updateControls();};
for(const id of ['load-more-observations','role-load-more'])el(id).onclick=()=>{if(activeTask&&observationCursor&&!sampleLoading)return loadProjectPreview(activeTask,observationCursor);};
el('observation-scope').onchange=()=>{observationScope=el('observation-scope').value;observationCursor=null;observationHasMore=false;preview.candidates=[];sampleLoading=true;inspect(null);renderSamples();renderCollaboration();if(activeTask)return loadProjectPreview(activeTask);};
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
  const purpose=el('purpose').value,projects=selection().map(project=>({tenant_id:project.tenant_id,sid:project.sid}));
  busy=true;updateControls();el('export-status').textContent='正在导出全部已保留过程…';
  try{
    const response=await fetch('/admin/api/training/export-observations',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({purpose,projects})});
    if(!response.ok){const error=await response.json();throw Error(error.detail||'导出失败');}
    if(!response.headers.get('content-type')?.includes('application/zip'))throw Error('服务器未返回 ZIP');
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='argus-all-observations.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    el('export-status').textContent='全部过程已下载，包含未验收、未结束与失败的保留记录。';
    renderAudit(await api('/admin/api/training/audit').catch(()=>({events:[]})));
  }catch(error){el('export-status').textContent='未完成导出：'+error.message;}
  finally{busy=false;updateControls();}
};

if(entryQuery.has('tenant'))el('tenant').value=entryQuery.get('tenant')||'';
load();
"""
