'use client'

import { useEffect, useRef, useState, type CSSProperties } from 'react'
import api from '../lib/api'

interface Props {
  onClose: () => void
  onConnected: () => void
}

export default function QrCodeModal({ onClose, onConnected }: Props) {
  const [qr, setQr] = useState<string>('')
  const [countdown, setCountdown] = useState(30)
  const [error, setError] = useState('')
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const renewPendingRef = useRef(false)

  function clearTimers() {
    if (pollingRef.current) clearInterval(pollingRef.current)
    if (countdownRef.current) clearInterval(countdownRef.current)
  }

  async function fetchQr(endpoint = '/whatsapp/connect') {
    setError('')
    setCountdown(30)
    try {
      const r = endpoint === '/whatsapp/connect'
        ? await api.post('/whatsapp/connect')
        : await api.get('/whatsapp/qrcode')
      if (r.data.qr) {
        setQr(r.data.qr)
      } else if (r.data.error) {
        setError(r.data.error)
      }
    } catch {
      setError('Erro ao obter QR code')
    }
  }

  useEffect(() => {
    fetchQr('/whatsapp/connect')

    // Polling de status a cada 3s
    pollingRef.current = setInterval(async () => {
      try {
        const r = await api.get('/whatsapp/status')
        if (r.data.status === 'connected') {
          clearTimers()
          onConnected()
        }
      } catch {}
    }, 3000)

    // Countdown de 30s — renovação disparada fora do updater para evitar side-effects no Strict Mode
    countdownRef.current = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) {
          renewPendingRef.current = true
          return 30
        }
        return prev - 1
      })
      if (renewPendingRef.current) {
        renewPendingRef.current = false
        fetchQr('/whatsapp/qrcode')
      }
    }, 1000)

    return clearTimers
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const overlay: CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
  }
  const modal: CSSProperties = {
    background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12,
    padding: 32, maxWidth: 420, width: '90%', position: 'relative',
  }

  return (
    <div style={overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={modal}>
        <button
          onClick={onClose}
          style={{ position: 'absolute', top: 12, right: 16, background: 'none', border: 'none', color: 'var(--muted)', fontSize: 20, cursor: 'pointer' }}
          aria-label="Fechar"
        >×</button>

        <h3 style={{ color: 'var(--text)', marginBottom: 8 }}>Conectar WhatsApp</h3>
        <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 20 }}>
          Escaneie o QR code com o WhatsApp no seu celular
        </p>

        {/* Instruções */}
        <ol style={{ color: 'var(--muted)', fontSize: 13, paddingLeft: 20, marginBottom: 20 }}>
          <li>Abra o WhatsApp no seu celular</li>
          <li>Toque em Menu (⋮) → Aparelhos conectados</li>
          <li>Toque em "Conectar um aparelho"</li>
          <li>Escaneie este QR code</li>
        </ol>

        {/* QR */}
        {error && <p style={{ color: 'var(--danger)', textAlign: 'center' }}>{error}</p>}
        {qr && !error && (
          <div style={{ textAlign: 'center', marginBottom: 16 }}>
            <img
              src={qr.startsWith('data:') ? qr : `data:image/png;base64,${qr}`}
              alt="QR Code WhatsApp"
              style={{ width: 260, height: 260, border: '4px solid var(--text)', borderRadius: 8 }}
            />
          </div>
        )}

        {/* Countdown */}
        <div style={{ marginBottom: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>
            <span>QR expira em</span>
            <span>{countdown}s</span>
          </div>
          <div style={{ background: 'var(--surface)', borderRadius: 4, height: 6 }}>
            <div style={{
              background: countdown > 10 ? 'var(--success)' : 'var(--warning)',
              width: `${(countdown / 30) * 100}%`,
              height: '100%', borderRadius: 4,
              transition: 'width 1s linear, background 0.3s',
            }} />
          </div>
        </div>
        <p style={{ color: 'var(--muted)', fontSize: 11, textAlign: 'center' }}>
          Novo QR code é gerado automaticamente ao expirar
        </p>
      </div>
    </div>
  )
}
