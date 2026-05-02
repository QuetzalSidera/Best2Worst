"use client";

import { useEffect, useState } from "react";
import StepLayout from "@/components/StepLayout";
import { apiPost, fetchBootstrap } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/backend";

export default function VideoPage() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetchBootstrap().then(setData).catch(console.error);
  }, []);

  if (!data) {
    return <StepLayout activeStep="video"><div className="panel">加载中...</div></StepLayout>;
  }

  return (
    <StepLayout activeStep="video">
      <div className="panel">
        <h2>获取视频</h2>
        <div className="muted">完成音频生成后，再单独生成最终视频。</div>
        <button onClick={() => apiPost("/api/jobs/video")}>生成最终视频</button>
        {data.final_video_exists ? (
          <>
            <video className="media" controls src={`${API_BASE}/video/final`} style={{ marginTop: 16 }} />
            <a href={`${API_BASE}/video/final`} download="final_video.mp4">
              <button type="button">下载最终视频</button>
            </a>
          </>
        ) : (
          <div className="muted" style={{ marginTop: 14 }}>当前还没有生成最终视频。</div>
        )}
      </div>
    </StepLayout>
  );
}
