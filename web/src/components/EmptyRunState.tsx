import emptyAgentIcon from '../assets/empty-agent.png'
import emptyChecksIcon from '../assets/empty-checks.png'
import emptyLogsIcon from '../assets/empty-logs.png'

interface EmptyRunStateProps {
  kind: 'agent' | 'checks' | 'logs'
  onGoToTraffic: () => void
}

const COPY = {
  agent: {
    icon: emptyAgentIcon,
    alt: '',
    description: 'Chat tools become available after creating a run.',
  },
  checks: {
    icon: emptyChecksIcon,
    alt: '',
    description: 'Create a run to make the sandbox checks available.',
  },
  logs: {
    icon: emptyLogsIcon,
    alt: '',
    description: 'Access log entries will appear after the first run.',
  },
} as const

export function EmptyRunState({ kind, onGoToTraffic }: EmptyRunStateProps) {
  const cfg = COPY[kind]
  return (
    <section className="empty-panel" aria-label="No run selected">
      <img src={cfg.icon} alt={cfg.alt} className="empty-panel-icon" aria-hidden="true" />
      <h2>No run selected</h2>
      <p>{cfg.description}</p>
      <button type="button" className="btn btn-primary" onClick={onGoToTraffic}>
        Go to Traffic →
      </button>
    </section>
  )
}
