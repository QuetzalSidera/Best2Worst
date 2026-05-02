"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";

const STEPS = [
  ["setup", "1. 准备"],
  ["materials", "2. 素材上传"],
  ["copywriting", "3. 文案确认"],
  ["audio", "4. 音频生成"],
  ["video", "5. 获取视频"],
];

export default function StepLayout({ activeStep, children }) {
  const [state, setState] = useState({
    busy: false,
    stage: "idle",
    progress: 0,
    message: "",
    error: "",
    logs: [],
  });

  useEffect(() => {
    let mounted = true;
    let previousBusy = false;
    let previousStage = "idle";
    const poll = async () => {
      try {
        const nextState = await apiGet("/api/state");
        if (mounted) {
          if (previousBusy && !nextState.busy && ["copywriting", "audio", "video"].includes(previousStage) && !nextState.error) {
            window.location.reload();
            return;
          }
          previousBusy = nextState.busy;
          previousStage = nextState.busy ? nextState.stage : previousStage;
          setState(nextState);
        }
      } catch {
        // no-op
      }
    };
    poll();
    const timer = setInterval(poll, 1500);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="wrap">
      <div className="hero">
        <div className="panel">
          <div className="topbar">
            <div>
              <h1>从夯到拉锐评生成器</h1>
              <div className="muted">按顺序完成：准备 -&gt; 素材上传 -&gt; 文案确认 -&gt; 音频生成 -&gt; 获取视频。</div>
            </div>
          </div>
          <div className="status-grid">
            <div>
              <div>
                <span className="pill">状态: <strong>{state.busy ? "运行中" : "空闲"}</strong></span>
                <span className="pill">阶段: <strong>{state.stage}</strong></span>
                <span className="pill">进度: <strong>{Math.round(state.progress || 0)}%</strong></span>
              </div>
              <div className="progress-row">
                <div className="progress-shell">
                  <div className="progress-bar" style={{ width: `${state.progress || 0}%` }} />
                </div>
                {state.busy ? <div className="spinner" /> : <div />}
              </div>
              <div style={{ marginTop: 10 }}>
                <div>{state.message}</div>
                <div style={{ color: "#b00020" }}>{state.error}</div>
              </div>
            </div>
            <div>
              <div className="actions">
                <button className="ghost" onClick={() => apiPost("/api/reset-output").then(() => window.location.reload())}>清空文案与结果</button>
                <button className="ghost" onClick={() => apiPost("/api/reset-project").then(() => window.location.assign("/materials"))}>开始下一个视频</button>
              </div>
              <div className="logbox">{(state.logs || []).join("\n")}</div>
            </div>
          </div>
        </div>
      </div>

      <div className="tabs">
        {STEPS.map(([key, label]) => (
          <Link key={key} href={`/${key}`} className={`tab-button ${activeStep === key ? "active" : ""}`}>
            {label}
          </Link>
        ))}
      </div>

      {children}
    </div>
  );
}
