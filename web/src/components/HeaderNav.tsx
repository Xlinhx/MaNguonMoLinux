import { FixtureMode } from '../fixtures/mockupData'
import { Run, TabId } from '../types'
import { BottomSheet } from './BottomSheet'
import { LogoMark } from './LogoMark'
import { RunSelector } from './RunSelector'

const NAV_ITEMS: Array<{ id: TabId; label: string }> = [
  { id: 'traffic', label: 'Traffic' },
  { id: 'agent', label: 'Agent' },
  { id: 'checks', label: 'Checks' },
  { id: 'logs', label: 'Access log' },
]

interface HeaderNavProps {
  activeTab: TabId
  onNavigate: (tab: TabId) => void
  selectedRun: Run | null
  hasRuns: boolean
  isMobile: boolean
  runSelectorOpen: boolean
  onToggleRunSelector: () => void
  onCloseRunSelector: () => void
  mobileNavOpen: boolean
  onOpenMobileNav: () => void
  onCloseMobileNav: () => void
  onSelectRun: (runId: string) => void
  fixtureMode: FixtureMode
  refreshNonce: number
}

export function HeaderNav({
  activeTab,
  onNavigate,
  selectedRun,
  hasRuns,
  isMobile,
  runSelectorOpen,
  onToggleRunSelector,
  onCloseRunSelector,
  mobileNavOpen,
  onOpenMobileNav,
  onCloseMobileNav,
  onSelectRun,
  fixtureMode,
  refreshNonce,
}: HeaderNavProps) {
  return (
    <>
      <header className="top-header">
        <div className="container top-header-inner">
          <a
            href="#traffic"
            className="brand-link"
            onClick={(e) => {
              e.preventDefault()
              onNavigate('traffic')
            }}
          >
            <LogoMark />
            <span>Sandbox Lab</span>
          </a>

          <nav className="desktop-nav" aria-label="Main navigation">
            {NAV_ITEMS.map((item) => {
              const active = item.id === activeTab
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`nav-tab ${active ? 'is-active' : ''}`}
                  aria-current={active ? 'page' : undefined}
                  onClick={() => onNavigate(item.id)}
                >
                  {item.label}
                </button>
              )
            })}
          </nav>

          {!isMobile && (
            <div className="header-right">
              {hasRuns && selectedRun ? (
                <button
                  type="button"
                  data-run-trigger="true"
                  className="run-trigger"
                  aria-expanded={runSelectorOpen}
                  aria-haspopup="dialog"
                  onClick={onToggleRunSelector}
                >
                  <span>{selectedRun.label}</span>
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                    <path
                      d="M3.2 5.2L7 9L10.8 5.2"
                      stroke="currentColor"
                      strokeWidth="1.7"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>
              ) : (
                <span className="run-empty-pill">No runs yet</span>
              )}
              <RunSelector
                open={runSelectorOpen}
                isMobile={false}
                selectedRun={selectedRun}
                fixtureMode={fixtureMode}
                onClose={onCloseRunSelector}
                onSelectRun={onSelectRun}
                refreshNonce={refreshNonce}
              />
            </div>
          )}

          <button
            type="button"
            className="mobile-menu-btn"
            aria-label="Open navigation menu"
            aria-expanded={mobileNavOpen}
            onClick={onOpenMobileNav}
          >
            <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
              <path
                d="M3.5 6H18.5M3.5 11H18.5M3.5 16H18.5"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </header>

      {isMobile && (
        <>
          <BottomSheet open={mobileNavOpen} title="Navigation" onClose={onCloseMobileNav}>
            <div className="nav-sheet-list">
              {NAV_ITEMS.map((item) => {
                const active = item.id === activeTab
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`nav-sheet-item ${active ? 'is-active' : ''}`}
                    aria-current={active ? 'page' : undefined}
                    onClick={() => {
                      onNavigate(item.id)
                      onCloseMobileNav()
                    }}
                  >
                    <span>{item.label}</span>
                    {active && (
                      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
                        <path
                          d="M4 9.5L7.3 12.8L14 5.5"
                          stroke="#0065C9"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    )}
                  </button>
                )
              })}
            </div>
          </BottomSheet>

          <RunSelector
            open={runSelectorOpen}
            isMobile={true}
            selectedRun={selectedRun}
            fixtureMode={fixtureMode}
            onClose={onCloseRunSelector}
            onSelectRun={onSelectRun}
            refreshNonce={refreshNonce}
          />
        </>
      )}
    </>
  )
}
