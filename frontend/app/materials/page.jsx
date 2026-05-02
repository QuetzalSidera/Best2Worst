"use client";

import { useEffect, useState } from "react";
import StepLayout from "@/components/StepLayout";
import { apiPost, fetchBootstrap } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/backend";

export default function MaterialsPage() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetchBootstrap().then(setData).catch(console.error);
  }, []);

  if (!data) {
    return <StepLayout activeStep="materials"><div className="panel">加载中...</div></StepLayout>;
  }

  const saveMaterials = async (materials) => {
    const payload = { materials: materials.map(({ id, display_name, forced_rank }) => ({ id, display_name, forced_rank })) };
    const next = await apiPost("/api/materials/save", payload);
    setData({ ...data, materials: next.materials });
  };

  const moveMaterial = async (index, direction) => {
    const targetIndex = index + direction;
    if (targetIndex < 0 || targetIndex >= data.materials.length) return;
    const nextMaterials = [...data.materials];
    [nextMaterials[index], nextMaterials[targetIndex]] = [nextMaterials[targetIndex], nextMaterials[index]];
    setData({ ...data, materials: nextMaterials });
    await saveMaterials(nextMaterials);
  };

  const onUpload = async (event) => {
    const form = new FormData();
    for (const file of event.target.files || []) {
      form.append("files", file);
    }
    const next = await apiPost("/api/materials/upload", form);
    setData({ ...data, materials: next.materials });
  };

  return (
    <StepLayout activeStep="materials">
      <div className="panel">
        <h2>素材上传与排序</h2>
        <input type="file" multiple accept="image/*" onChange={onUpload} />
        <div className="material-list" style={{ marginTop: 16 }}>
          {data.materials.map((item) => (
            <div className="material-item" key={item.id}>
              <img src={`${API_BASE}/material/${item.id}`} alt={item.display_name} />
              <div>
                <label>名称</label>
                <input
                  value={item.display_name}
                  onChange={(e) => setData({ ...data, materials: data.materials.map((m) => m.id === item.id ? { ...m, display_name: e.target.value } : m) })}
                  onBlur={() => saveMaterials(data.materials)}
                />
              </div>
              <div>
                <label>指定等级</label>
                <select
                  value={item.forced_rank || ""}
                  onChange={(e) => setData({ ...data, materials: data.materials.map((m) => m.id === item.id ? { ...m, forced_rank: e.target.value } : m) })}
                  onBlur={() => saveMaterials(data.materials)}
                >
                  {["", "夯", "顶级", "人上人", "NPC", "拉完了"].map((rank) => <option key={rank} value={rank}>{rank || "由 AI 决定"}</option>)}
                </select>
              </div>
              <div>
                <button className="ghost" onClick={() => moveMaterial(data.materials.findIndex((m) => m.id === item.id), -1)}>上移</button>
                <button className="ghost" onClick={() => moveMaterial(data.materials.findIndex((m) => m.id === item.id), 1)}>下移</button>
                <button
                  className="ghost"
                  onClick={async () => {
                    const next = await apiPost(`/api/materials/delete/${item.id}`);
                    setData({ ...data, materials: next.materials });
                  }}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </StepLayout>
  );
}
