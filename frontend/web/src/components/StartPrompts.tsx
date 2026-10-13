import { FileText, ChartColumn, PanelsTopLeft, Search } from 'lucide-react';
import { useI18n } from '../i18n';

const starters = [
  { icon: Search, zh: ['整理资料', '上传文档，提炼结论与来源', '请帮我整理接下来提供的资料，提炼主要结论、依据和待确认的问题。先确认需要哪些材料，不要编造缺失的信息。'], en: ['Understand documents', 'Bring your files; get findings and sources', 'Help me organize the material I provide next into findings, sources and open questions. First confirm what you need; do not invent missing information.'] },
  { icon: ChartColumn, zh: ['分析表格', '上传 CSV，得到分析和可下载结果', '请分析我接下来上传的表格，先确认字段含义和分析目标，再给出结论、图表或可下载的结果。不要使用虚构数据代替我的文件。'], en: ['Analyze a table', 'Upload a CSV for findings and usable results', 'Analyze the table I upload next. Confirm the columns and my goal first, then provide findings and downloadable results. Do not replace my file with invented data.'] },
  { icon: FileText, zh: ['制作报告', '说明读者与目标，生成可阅读的文档', '我想制作一份报告。请先问清主题、读者、用途和可用材料，再给出结构建议。确认后生成可以直接阅读和下载的报告。'], en: ['Prepare a report', 'Define the audience and the outcome', 'Help me prepare a report. Ask about the topic, audience, purpose and source material, then propose an outline. After confirmation, create a readable, downloadable report.'] },
  { icon: PanelsTopLeft, zh: ['制作交互页面', '在浏览器里直接打开和试用', '我想制作一个交互页面。请先确认用途、内容和交互要求。最终需要能在当前浏览器直接打开的成果，不要只给我需要在服务器运行的命令。'], en: ['Build an interactive page', 'Open and use the result in your browser', 'Help me build an interactive page. First confirm its purpose, content and interactions. Deliver a result I can open in this browser, not only commands to run on a server.'] },
];

export function StartPrompts({ onPick, disabled = false }: { onPick: (text: string) => void; disabled?: boolean }) {
  const { locale } = useI18n();
  return <div className="starter-grid">
    {starters.map(({ icon: Icon, zh, en }) => {
      const [title, hint, prompt] = locale === 'zh-CN' ? zh : en;
      return <button type="button" key={title} className="starter-card" onClick={() => onPick(prompt)} disabled={disabled}>
        <Icon size={19} aria-hidden="true" /><span><strong>{title}</strong><small>{hint}</small></span>
      </button>;
    })}
  </div>;
}
