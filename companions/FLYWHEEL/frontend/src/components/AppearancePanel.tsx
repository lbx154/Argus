import { Check, Languages, Monitor, Moon, Palette, Sun } from "lucide-react";

import { usePreferences } from "../lib/preferences";

const languageOptions = [
  {
    value: "zh-CN",
    label: "中文",
    description: "简体中文",
  },
  {
    value: "en-US",
    label: "English",
    description: "English",
  },
] as const;

const themeOptions = [
  { value: "dark", icon: Moon },
  { value: "light", icon: Sun },
  { value: "system", icon: Monitor },
] as const;

export function AppearancePanel() {
  const { locale, setLocale, theme, setTheme, resolvedTheme } =
    usePreferences();
  const isChinese = locale === "zh-CN";
  const copy = isChinese
    ? {
        title: "外观与语言",
        description: "语言与主题只改变界面呈现，不会改变研究记录或运行状态。",
        language: "界面语言",
        theme: "主题模式",
        dark: "暗色",
        light: "亮色",
        system: "跟随系统",
        darkDescription: "观测站暗色",
        lightDescription: "实验室亮色",
        systemDescription: "使用设备设置",
        resolved: "当前显示",
      }
    : {
        title: "Appearance & language",
        description:
          "Language and theme change presentation only, never research records or runtime state.",
        language: "Interface language",
        theme: "Theme mode",
        dark: "Dark",
        light: "Light",
        system: "System",
        darkDescription: "Observatory dark",
        lightDescription: "Laboratory light",
        systemDescription: "Use this device's setting",
        resolved: "Currently displayed",
      };
  const themeLabels = {
    dark: copy.dark,
    light: copy.light,
    system: copy.system,
  } as const;
  const themeDescriptions = {
    dark: copy.darkDescription,
    light: copy.lightDescription,
    system: copy.systemDescription,
  } as const;

  return (
    <section className="appearance-panel" aria-labelledby="appearance-title">
      <header>
        <Palette size={18} aria-hidden="true" />
        <div>
          <h2 id="appearance-title">{copy.title}</h2>
          <p>{copy.description}</p>
        </div>
      </header>

      <fieldset>
        <legend>
          <Languages size={15} aria-hidden="true" />
          {copy.language}
        </legend>
        <div className="appearance-options">
          {languageOptions.map((option) => {
            const selected = locale === option.value;
            return (
              <button
                key={option.value}
                type="button"
                className={`appearance-option${selected ? " selected" : ""}`}
                aria-pressed={selected}
                onClick={() => setLocale(option.value)}
              >
                <span aria-hidden="true">{option.value === "zh-CN" ? "中" : "A"}</span>
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.description}</small>
                </span>
                {selected && <Check size={15} aria-hidden="true" />}
              </button>
            );
          })}
        </div>
      </fieldset>

      <fieldset>
        <legend>{copy.theme}</legend>
        <div className="appearance-options">
          {themeOptions.map(({ value, icon: Icon }) => {
            const selected = theme === value;
            return (
              <button
                key={value}
                type="button"
                className={`appearance-option${selected ? " selected" : ""}`}
                aria-pressed={selected}
                onClick={() => setTheme(value)}
              >
                <span className={`theme-preview ${value}`} aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                <span>
                  <strong>
                    <Icon size={14} aria-hidden="true" />
                    {themeLabels[value]}
                  </strong>
                  <small>{themeDescriptions[value]}</small>
                </span>
                {selected && <Check size={15} aria-hidden="true" />}
              </button>
            );
          })}
        </div>
      </fieldset>

      <div className="preference-summary" role="status" aria-live="polite">
        {resolvedTheme === "dark" ? (
          <Moon size={15} aria-hidden="true" />
        ) : (
          <Sun size={15} aria-hidden="true" />
        )}
        <span>{copy.resolved}</span>
        <strong>{themeLabels[resolvedTheme]}</strong>
      </div>
    </section>
  );
}
