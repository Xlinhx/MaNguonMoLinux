interface PaginationBarProps {
  start: number
  end: number
  total: number
  hasPrev: boolean
  hasNext: boolean
  onPrev: () => void
  onNext: () => void
  compact?: boolean
}

export function PaginationBar({
  start,
  end,
  total,
  hasPrev,
  hasNext,
  onPrev,
  onNext,
  compact = false,
}: PaginationBarProps) {
  return (
    <div className="table-pagination">
      <span className="sequence-range">
        {total === 0 ? '0 of 0' : `${start}–${end} of ${total}`}
      </span>
      <div className="arrow-group">
        {compact ? (
          <>
            <button
              type="button"
              className="icon-page-btn"
              aria-label="Previous"
              disabled={!hasPrev}
              onClick={onPrev}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M10 3.5L5.5 8L10 12.5"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
            <button
              type="button"
              className="icon-page-btn"
              aria-label="Next"
              disabled={!hasNext}
              onClick={onNext}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M6 3.5L10.5 8L6 12.5"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn btn-outline"
              style={{ minHeight: 38, padding: '0 14px' }}
              disabled={!hasPrev}
              onClick={onPrev}
            >
              Previous
            </button>
            <button
              type="button"
              className="btn btn-outline"
              style={{ minHeight: 38, padding: '0 14px' }}
              disabled={!hasNext}
              onClick={onNext}
            >
              Next
            </button>
          </>
        )}
      </div>
    </div>
  )
}
