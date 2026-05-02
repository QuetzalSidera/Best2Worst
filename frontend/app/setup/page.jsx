"use client";

import { useEffect, useState } from "react";
import StepLayout from "@/components/StepLayout";
import { apiPost, fetchBootstrap } from "@/lib/api";

export default function SetupPage() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetchBootstrap().then(setData).catch(console.error);
  }, []);

  if (!data) {
    return <StepLayout activeStep="setup"><div className="panel">加载中...</div></StepLayout>;
  }

  const apiKeys = data.api_keys;
  const settings = data.settings;

  const save = async (patch) => {
    const payload = {
      api_keys: { ...apiKeys, ...(patch.api_keys || {}) },
      settings: { ...settings, ...(patch.settings || {}) },
    };
    const next = await apiPost("/api/setup", payload);
    setData(next.data);
  };

  const uploadCustomVoice = async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = await apiPost("/api/custom-voice", form);
    setData({
      ...data,
      custom_voices: next.custom_voices,
      current_voice: next.current_voice,
      settings: { ...settings, tts_voice: next.current_voice },
    });
  };

  return (
    <StepLayout activeStep="setup">
      <div className="tab-layout">
        <div className="panel">
          <h2>常规配置</h2>
          <div className="two-col">
            <div>
              <label>DeepSeek API Key</label>
              <input value={apiKeys.deepseek_api_key} onChange={(e) => setData({ ...data, api_keys: { ...apiKeys, deepseek_api_key: e.target.value } })} onBlur={() => save({})} />
            </div>
            <div>
              <label>硅基流动 API Key</label>
              <input value={apiKeys.siliconflow_api_key} onChange={(e) => setData({ ...data, api_keys: { ...apiKeys, siliconflow_api_key: e.target.value } })} onBlur={() => save({})} />
            </div>
            <div>
              <label>文案模型</label>
              <input value={settings.deepseek_model} onChange={(e) => setData({ ...data, settings: { ...settings, deepseek_model: e.target.value } })} onBlur={() => save({})} />
            </div>
            <div>
              <label>配音模型</label>
              <input value={settings.tts_model} onChange={(e) => setData({ ...data, settings: { ...settings, tts_model: e.target.value } })} onBlur={() => save({})} />
            </div>
            <div>
              <label>配音速度</label>
              <div className="slider-wrap">
                <input type="range" min="0.5" max="1.5" step="0.05" value={settings.tts_speed} onChange={(e) => setData({ ...data, settings: { ...settings, tts_speed: e.target.value } })} onMouseUp={() => save({})} />
                <input readOnly value={settings.tts_speed} />
              </div>
            </div>
            <div>
              <label>语音类型</label>
              <select value={settings.tts_voice} onChange={(e) => setData({ ...data, settings: { ...settings, tts_voice: e.target.value } })} onBlur={() => save({})}>
                {Object.entries(data.siliconflow_voices).map(([key, voice]) => <option key={key} value={key}>{voice.label}</option>)}
                {Object.entries(data.builtin_voices).map(([key, voice]) => <option key={key} value={key}>内置 - {voice.label}</option>)}
                {data.custom_voices.map((voice) => <option key={voice.id} value={`custom:${voice.id}`}>自定义 - {voice.reference_name}</option>)}
              </select>
            </div>
            <div className="two-col" style={{ gridColumn: "1 / -1" }}>
              <div style={{ gridColumn: "1 / -1" }}>
                <label>提示正文</label>
                <textarea value={settings.prompt_body} onChange={(e) => setData({ ...data, settings: { ...settings, prompt_body: e.target.value } })} onBlur={() => save({})} />
              </div>
            </div>
          </div>
        </div>
        <div className="panel">
          <h2>高级配置</h2>
          <div className="two-col">
            <div>
              <label>TTS Gain</label>
              <input value={settings.tts_gain} onChange={(e) => setData({ ...data, settings: { ...settings, tts_gain: e.target.value } })} onBlur={() => save({})} />
            </div>
            <div>
              <label>TTS Format</label>
              <input value={settings.tts_format} onChange={(e) => setData({ ...data, settings: { ...settings, tts_format: e.target.value } })} onBlur={() => save({})} />
            </div>
            <div>
              <label>TTS Sample Rate</label>
              <input value={settings.tts_sample_rate} onChange={(e) => setData({ ...data, settings: { ...settings, tts_sample_rate: e.target.value } })} onBlur={() => save({})} />
            </div>
          </div>
          <h3>当前可用音频</h3>
          {Object.values(data.siliconflow_voices).map((voice) => <div className="pill" key={voice.voice}>{voice.label}</div>)}
          {Object.values(data.builtin_voices).map((voice) => <div className="pill" key={voice.filename}>{voice.label}</div>)}
          {data.custom_voices.map((voice) => <div className="pill" key={voice.id}>自定义 - {voice.reference_name}</div>)}
          <form onSubmit={uploadCustomVoice}>
            <label>自定义音频文件</label>
            <input name="voice_file" type="file" accept="audio/*" />
            <label>对应文本</label>
            <textarea name="reference_text" placeholder="必须填写与音频完全对应的文本" />
            <label>名称</label>
            <input name="reference_name" defaultValue="custom-reference" />
            <button type="submit">上传并启用自定义音频</button>
          </form>
        </div>
      </div>
    </StepLayout>
  );
}
