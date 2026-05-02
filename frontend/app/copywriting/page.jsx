"use client";

import { useEffect, useState } from "react";
import StepLayout from "@/components/StepLayout";
import { apiPost, fetchBootstrap } from "@/lib/api";

export default function CopywritingPage() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetchBootstrap().then(setData).catch(console.error);
  }, []);

  if (!data) {
    return <StepLayout activeStep="copywriting"><div className="panel">加载中...</div></StepLayout>;
  }

  const saveCopywriting = async () => {
    await apiPost("/api/copywriting/save", {
      intro_text: data.intro_text,
      outro_text: data.outro_text,
      cards: data.copywriting_cards,
    });
  };

  const updateCard = (cardKey, updater) => {
    setData((current) => ({
      ...current,
      copywriting_cards: current.copywriting_cards.map((item) =>
        item.key === cardKey ? updater(item) : item,
      ),
    }));
  };

  return (
    <StepLayout activeStep="copywriting">
      <div className="panel">
        <h2>文案生成与确认</h2>
        <button type="button" onClick={() => apiPost("/api/jobs/copywriting")}>生成初稿文案</button>
        <label>开场文案</label>
        <textarea value={data.intro_text} onChange={(e) => setData({ ...data, intro_text: e.target.value })} />
        <div className="copy-grid">
          {data.copywriting_cards.map((card) => (
            <div className="copy-card" key={card.key}>
              <div className="copy-header">
                <h3>{card.display_name}</h3>
                <span className="pill" style={{ margin: 0 }}>项目 {card.key}</span>
              </div>
              <label>评级</label>
              <select
                value={card.rank}
                onChange={(e) => updateCard(card.key, (item) => ({ ...item, rank: e.target.value }))}
              >
                {["夯", "顶级", "人上人", "NPC", "拉完了"].map((rank) => <option key={rank} value={rank}>{rank}</option>)}
              </select>
              {card.texts.map((text, index) => (
                <div className="copy-line" key={`${card.key}-${index}`}>
                  <textarea
                    value={text}
                    onChange={(e) =>
                      updateCard(card.key, (item) => ({
                        ...item,
                        texts: item.texts.map((line, lineIndex) =>
                          lineIndex === index ? e.target.value : line,
                        ),
                      }))
                    }
                  />
                  <div className="line-actions">
                    <button
                      type="button"
                      className="ghost"
                      onClick={() =>
                        updateCard(card.key, (item) => ({
                          ...item,
                          texts: item.texts.length > 1
                            ? item.texts.filter((_, lineIndex) => lineIndex !== index)
                            : item.texts,
                        }))
                      }
                    >
                      删除
                    </button>
                  </div>
                </div>
              ))}
              <button
                type="button"
                className="ghost"
                onClick={() =>
                  updateCard(card.key, (item) => ({
                    ...item,
                    texts: [...item.texts, ""],
                  }))
                }
              >
                新增一句
              </button>
            </div>
          ))}
        </div>
        <label>结尾文案</label>
        <textarea value={data.outro_text} onChange={(e) => setData({ ...data, outro_text: e.target.value })} />
        <button type="button" onClick={saveCopywriting}>保存文案确认结果</button>
      </div>
    </StepLayout>
  );
}
