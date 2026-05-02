"use client";

import { useEffect, useState } from "react";
import StepLayout from "@/components/StepLayout";
import { apiPost, fetchBootstrap } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/backend";

export default function AudioPage() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetchBootstrap().then(setData).catch(console.error);
  }, []);

  if (!data) {
    return <StepLayout activeStep="audio"><div className="panel">加载中...</div></StepLayout>;
  }

  return (
    <StepLayout activeStep="audio">
      <div className="panel">
        <h2>音频生成</h2>
        <div className="muted">完成文案确认后，单独生成完整配音。</div>
        <button onClick={() => apiPost("/api/jobs/audio")}>生成完整音频</button>
        {data.full_audio_exists ? (
          <audio className="media" controls src={`${API_BASE}/audio/final`} style={{ marginTop: 16 }} />
        ) : (
          <div className="muted" style={{ marginTop: 14 }}>当前还没有生成完整音频。</div>
        )}
      </div>
    </StepLayout>
  );
}
