'use client';

import { useEffect, useRef, useState } from 'react';
import { Volume2, VolumeX } from 'lucide-react';

export function MTFTriggerAudio({ enabled, active, onChange }: {
  enabled: boolean; active: boolean; onChange: (enabled: boolean) => void;
}) {
  const context = useRef<AudioContext | null>(null);
  const buffer = useRef<Promise<AudioBuffer> | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');

  // Resume synchronously from the user's click, including after a page reload.
  const toggle = () => {
    if (enabled && ready) { onChange(false); return; }
    const ac = context.current ??= new AudioContext();
    ac.onstatechange = () => setReady(ac.state === 'running');
    void ac.resume().then(() => { setReady(ac.state === 'running'); setError(''); })
      .catch(() => setError('Clique para liberar o áudio no navegador.'));
    onChange(true);
  };

  useEffect(() => {
    const ac = context.current;
    if (!enabled || !active || !ready || !ac) return;
    let cancelled = false;
    let source: AudioBufferSourceNode | undefined;
    buffer.current ??= fetch('/audio/gatilho.mp3').then(response => {
      if (!response.ok) throw new Error('Falha ao carregar áudio');
      return response.arrayBuffer();
    }).then(bytes => ac.decodeAudioData(bytes));
    void buffer.current.then(decoded => {
      if (cancelled) return;
      source = ac.createBufferSource();
      source.buffer = decoded;
      source.loop = true;
      source.connect(ac.destination);
      source.start();
      setError('');
    }).catch(() => {
      buffer.current = null;
      if (!cancelled) setError('Não foi possível carregar o alerta sonoro. Desative e ative para tentar novamente.');
    });
    return () => { cancelled = true; source?.stop(); source?.disconnect(); };
  }, [enabled, active, ready]);

  useEffect(() => () => {
    const ac = context.current;
    if (ac) { ac.onstatechange = null; void ac.close(); }
    context.current = null;
    buffer.current = null;
  }, []);

  const label = enabled && ready ? 'Silenciar alerta sonoro' : 'Ativar alerta sonoro';
  return <>
    <button aria-label={label} aria-pressed={enabled && ready} title="Repetir alerta enquanto houver Gatilho na watchlist atual" onClick={toggle}>
      {enabled && ready ? <Volume2 size={15}/> : <VolumeX size={15}/>}
    </button>
    {error ? <small role="status">{error}</small> : enabled && !ready ? <small role="status">Clique no som para liberar o áudio.</small> : null}
  </>;
}
