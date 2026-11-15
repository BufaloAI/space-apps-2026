import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import type { Translate } from './model'
export function Brand() {
  return (
    <span className="brand">
      <img src="/brand.svg" alt="" />
      <span>
        BUFALO<b>AI</b>
      </span>
    </span>
  )
}
export function Dialog({
  title,
  children,
  close,
  t,
}: {
  title: string
  children: ReactNode
  close: () => void
  t: Translate
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    ref.current?.showModal()
  }, [])
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault()
        close()
      }}
      onClick={(e) => {
        if (e.target === ref.current) close()
      }}
    >
      <div className="dialog-in">
        <div className="dialog-head">
          <h2>{title}</h2>
          <button
            className="iconbtn"
            aria-label={t('Kapat', 'Close')}
            onClick={close}
          >
            <X size={19} />
          </button>
        </div>
        {children}
      </div>
    </dialog>
  )
}
export function LanguageSwitch({
  lang,
  change,
}: {
  lang: string
  change: () => void
}) {
  return (
    <button
      className="lang"
      onClick={change}
      aria-label={lang === 'tr' ? 'Switch to English' : 'Türkçeye geç'}
    >
      <span className={lang === 'tr' ? 'on' : ''}>TR</span>
      <i>/</i>
      <span className={lang === 'en' ? 'on' : ''}>EN</span>
    </button>
  )
}
