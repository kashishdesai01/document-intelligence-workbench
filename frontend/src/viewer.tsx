import { ChevronLeft, ChevronRight, ScanLine } from "lucide-react";
import { useEffect, useRef, useState, type PointerEvent } from "react";

import type { Box, Client, Field, Page } from "./api";
import { Alert, label, Loading, message } from "./ui";

type Point = { x: number; y: number };

export function Viewer({ api, id, generation, page, count, fields, selected, drawing, box, go, select, draw }: {
  api: Client; id: string; generation: number; page: number; count: number; fields: Field[];
  selected: string | null; drawing: boolean; box: Box | null; go: (page: number) => void;
  select: (key: string) => void; draw: (box: Box) => void;
}) {
  const [data, setData] = useState<{ number: number; metadata: Page; url: string }>();
  const [error, setError] = useState("");
  const [start, setStart] = useState<Point>();
  const [current, setCurrent] = useState<Point>();
  const stage = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setData(undefined);
    setError("");
    api.page(id, page, controller.signal).then(({ metadata, image }) => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(image);
      setData({ number: page, metadata, url: objectUrl });
    }).catch((error) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, id, page, generation]);

  useEffect(() => {
    const container = stage.current;
    if (!container || container.scrollHeight <= container.clientHeight || !selected || !data) return;
    const evidence = container.querySelector("rect.evidence.selected");
    if (!evidence) return;
    const region = evidence.getBoundingClientRect();
    const viewport = container.getBoundingClientRect();
    if (region.top < viewport.top || region.bottom > viewport.bottom) {
      container.scrollTop += (region.top + region.bottom - viewport.top - viewport.bottom) / 2;
    }
  }, [data, selected]);

  function point(event: PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const bound = (value: number) => Math.max(0, Math.min(1, value));
    return { x: bound((event.clientX - rect.left) / rect.width), y: bound((event.clientY - rect.top) / rect.height) };
  }

  function onDown(event: PointerEvent<SVGSVGElement>) {
    if (!drawing || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const next = point(event);
    setStart(next);
    setCurrent(next);
  }

  function toBox(first: Point, second: Point) {
    return { left: Math.min(first.x, second.x), right: Math.max(first.x, second.x),
      top: Math.min(first.y, second.y), bottom: Math.max(first.y, second.y) };
  }

  function onUp(event: PointerEvent<SVGSVGElement>) {
    if (drawing && start) {
      const next = toBox(start, point(event));
      if (next.right - next.left > 0.005 && next.bottom - next.top > 0.005) draw(next);
    }
    setStart(undefined);
    setCurrent(undefined);
  }

  const draft = drawing && start && current ? toBox(start, current) : box;
  const rect = (region: Box) => ({
    x: region.left * 1000, y: region.top * 1000,
    width: (region.right - region.left) * 1000, height: (region.bottom - region.top) * 1000,
  });

  return <section className="viewer panel" aria-label="Source document">
    <header className="viewer-toolbar">
      <div className="toolbar-label"><ScanLine size={17} /><strong>Source document</strong>
        {data?.number === page && <span className="source-tag">{data.metadata.source === "ocr" ? "OCR scan" : "Native PDF"}</span>}</div>
      <div className="pagination">
        <button className="icon-button" aria-label="Previous page" onClick={() => go(page - 1)} disabled={page <= 1}><ChevronLeft size={18} /></button>
        <span>Page {page} of {count}</span>
        <button className="icon-button" aria-label="Next page" onClick={() => go(page + 1)} disabled={page >= count}><ChevronRight size={18} /></button>
      </div>
    </header>
    <div className="page-stage" ref={stage}>
      {error && <Alert>{error}</Alert>}
      {!error && data?.number !== page && <Loading>Loading page…</Loading>}
      {data?.number === page && <div className="page-image" style={{ aspectRatio: `${data.metadata.width} / ${data.metadata.height}` }}>
        <img src={data.url} alt={`Statement source, page ${page}`} />
        <svg className={drawing ? "overlay drawing" : "overlay"} viewBox="0 0 1000 1000" preserveAspectRatio="none"
          aria-label="Field evidence boxes"
          onPointerDown={onDown} onPointerMove={(event) => { if (drawing && start) setCurrent(point(event)); }}
          onPointerUp={onUp} onPointerCancel={() => { setStart(undefined); setCurrent(undefined); }}>
          {[...fields.filter((field) => field.key !== selected), ...fields.filter((field) => field.key === selected)]
            .flatMap((field) => field.evidence.map((item, index) => item.page === page &&
              <rect key={`${field.key}-${index}`} {...rect(item.box)}
                className={`evidence ${field.status} ${field.key === selected ? "selected" : ""}`}
                aria-label={`Select ${label(field.key)}: ${field.value ?? "missing"}`} role="button"
                tabIndex={drawing ? -1 : 0}
                onKeyDown={(event) => { if (!drawing && (event.key === "Enter" || event.key === " ")) {
                  event.preventDefault(); select(field.key);
                } }}
                onClick={() => { if (!drawing) select(field.key); }} vectorEffect="non-scaling-stroke" />))}
          {draft && <rect className="draft-box" {...rect(draft)} vectorEffect="non-scaling-stroke" />}
        </svg>
      </div>}
    </div>
    <footer className="viewer-legend"><span><i />Accepted</span><span><i />Review</span><span><i />Corrected</span></footer>
  </section>;
}
