import { useMemo, useState } from 'react'

type AnomalySeverity = 'critical' | 'high' | 'medium' | 'low'

type AnomalyCategory =
  | 'new_employee'
  | 'salary_revision'
  | 'data_error'
  | 'missing_deduction'
  | 'missing_slip'
  | 'no_anomaly'

type HITLPreview = {
  employees_in_current_payroll: number
  total_anomalies: number
  breakdown: Record<Exclude<AnomalyCategory, 'no_anomaly'>, number>
  top_3_anomalies: Array<{
    employee_name: string
    prev_net_pay: number | null
    curr_net_pay: number | null
    pct_change: number | null
    category: AnomalyCategory
    severity: AnomalySeverity
  }>
  confirmation_prompt: string
}

type AnomalyReport = {
  generated_at: string
  period_current: string
  period_previous: string
  employees_evaluated: number
  employees_in_current_payroll: number
  total_anomalies: number
  threshold_pct: number
  summary: string
  agents_involved?: string[]
  anomalies: Array<{
    employee_name: string
    anomaly_category: AnomalyCategory
    severity: AnomalySeverity
    pct_change: number | null
    prev_net_pay: number | null
    curr_net_pay: number | null
    prev_deductions: number | null
    curr_deductions: number | null
    missing_deduction_components: string[]
    suggested_action: string
    llm_explanation: string | null
  }>
}

type RunApiResponse = {
  status: string
  session_id?: string | null
  preview?: HITLPreview | null
  message?: string | null
  available_periods?: string[] | null
  detail?: string | null
}

type AppState =
  | 'idle'
  | 'loading'
  | 'awaiting_confirmation'
  | 'completed'
  | 'cancelled'
  | 'empty'
  | 'error'

const categoryLabels: Record<AnomalyCategory, string> = {
  new_employee: 'New employee',
  salary_revision: 'Salary revision',
  data_error: 'Data error',
  missing_deduction: 'Missing deduction',
  missing_slip: 'Missing slip',
  no_anomaly: 'No anomaly',
}

const severityStyles: Record<AnomalySeverity, string> = {
  critical: 'bg-rose-100 text-rose-700 border-rose-200',
  high: 'bg-amber-100 text-amber-700 border-amber-200',
  medium: 'bg-blue-100 text-blue-700 border-blue-200',
  low: 'bg-emerald-100 text-emerald-700 border-emerald-200',
}

const apiBase = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000/api/v1'

const suggestedPrompts = [
  'Review this month payroll for missing deductions.',
  'Identify payroll inconsistencies and missing slips.',
  'Detect unusual salary revisions this cycle.',
  'Review payroll anomalies before finalization.',
]

const defaultAgents = [
  'Comparison Agent',
  'Deduction Analysis Agent',
  'Categorization Agent',
  'Report Builder Agent',
]

const payrollKeywords = [
  'payroll',
  'salary',
  'deduction',
  'employee',
  'audit',
  'compare',
  'anomaly',
  'review',
]

const hasPayrollIntent = (input: string) => {
  const lowered = input.toLowerCase()
  const hasKeyword = payrollKeywords.some((keyword) => lowered.includes(keyword))
  return hasKeyword
}

function App() {
  const [prompt, setPrompt] = useState('')
  const [status, setStatus] = useState<AppState>('idle')
  const [preview, setPreview] = useState<HITLPreview | null>(null)
  const [report, setReport] = useState<AnomalyReport | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [inlineValidation, setInlineValidation] = useState<string | null>(null)
  const [isConfirming, setIsConfirming] = useState(false)
  const [toastMessage, setToastMessage] = useState<string | null>(null)
  const [toastTone, setToastTone] = useState<'success' | 'muted'>('success')
  const [emptyMessage, setEmptyMessage] = useState<string | null>(null)
  const [availablePeriods, setAvailablePeriods] = useState<string[] | null>(null)

  const currencyFormatter = useMemo(
    () =>
      new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        maximumFractionDigits: 0,
      }),
    [],
  )

  const formatCurrency = (value: number | null) => {
    if (value === null || Number.isNaN(value)) return '—'
    return currencyFormatter.format(value)
  }

  const formatPercent = (value: number | null) => {
    if (value === null || Number.isNaN(value)) return '—'
    return `${value.toFixed(1)}%`
  }

  const showToast = (message: string, tone: 'success' | 'muted' = 'success') => {
    setToastMessage(message)
    setToastTone(tone)
    window.setTimeout(() => setToastMessage(null), 3600)
  }

  const buildPreviewSummary = (targetPreview: HITLPreview) =>
    `${targetPreview.total_anomalies} anomalies detected across ${targetPreview.employees_in_current_payroll} employees. Salary revisions and missing deductions require HR review before payroll finalization.`

  const runPayrollReview = async () => {
    if (!prompt.trim()) {
      setErrorMessage('Please enter a short prompt for the payroll review.')
      setStatus('error')
      return
    }

    if (!hasPayrollIntent(prompt)) {
      setInlineValidation('Please enter a payroll review instruction.')
      setErrorMessage(null)
      setStatus('idle')
      return
    }

    setInlineValidation(null)

    setErrorMessage(null)
    setStatus('loading')
    setReport(null)
    setPreview(null)
    setEmptyMessage(null)
    setAvailablePeriods(null)

    try {
      const response = await fetch(`${apiBase}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt.trim() }),
      })

      const payload = (await response.json()) as RunApiResponse

      if (!response.ok) {
        throw new Error(payload?.detail ?? 'Unable to start payroll review.')
      }

      if (payload.status === 'no_data') {
        setPreview(null)
        setReport(null)
        setSessionId(null)
        setStatus('empty')
        setEmptyMessage(
          payload.message ?? 'No payroll records were found for the selected period.',
        )
        setAvailablePeriods(payload.available_periods ?? null)
        showToast(payload.message ?? 'No payroll data found.', 'muted')
        return
      }

      setPreview(payload.preview ?? null)
      setSessionId(payload.session_id ?? null)
      setStatus(
        payload.status === 'awaiting_confirmation' ? 'awaiting_confirmation' : 'idle',
      )
      showToast('Payroll review ready for HR confirmation.')
    } catch (error) {
      const message =
        error instanceof Error && error.message.includes('Failed to fetch')
          ? 'Backend unavailable. Please check the connection and try again.'
          : error instanceof Error
            ? error.message
            : 'Something went wrong.'
      setErrorMessage(message)
      setStatus('error')
    }
  }

  const resolveReview = async (confirmed: boolean) => {
    if (!sessionId) return

    setIsConfirming(true)
    setErrorMessage(null)

    try {
      const response = await fetch(`${apiBase}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, confirmed }),
      })

      const payload = await response.json()

      if (!response.ok) {
        throw new Error(payload?.detail ?? 'Unable to resolve review.')
      }

      if (payload.status === 'completed') {
        setReport(payload.report ?? null)
        setStatus('completed')
        showToast('Payroll report finalized and ready to share.')
      } else if (payload.status === 'cancelled') {
        setStatus('cancelled')
        showToast('Payroll review cancelled.', 'muted')
      }
    } catch (error) {
      const message =
        error instanceof Error && error.message.includes('Failed to fetch')
          ? 'Backend unavailable. Please check the connection and try again.'
          : error instanceof Error
            ? error.message
            : 'Something went wrong.'
      setErrorMessage(message)
      setStatus('error')
    } finally {
      setIsConfirming(false)
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-slate-50 text-slate-900">
      <div className="pointer-events-none absolute -top-24 right-10 h-72 w-72 rounded-full bg-sky-200/60 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 left-10 h-80 w-80 rounded-full bg-slate-200/70 blur-3xl" />
      {toastMessage && (
        <div className="pointer-events-none fixed right-6 top-6 z-10">
          <div
            className={`rounded-full px-4 py-2 text-xs font-semibold shadow-lg ${
              toastTone === 'success'
                ? 'bg-slate-900 text-white'
                : 'bg-white text-slate-600 border border-slate-200'
            }`}
          >
            {toastMessage}
          </div>
        </div>
      )}
      <div className="mx-auto flex min-h-screen w-full max-w-5xl flex-col px-6 pb-20 pt-10 sm:px-8">
        <nav className="flex flex-wrap items-center justify-between gap-4">
          <div className="text-lg font-semibold tracking-tight text-slate-900">
            Payroll Review Assistant
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-2 rounded-full border border-blue-100 bg-white/80 px-3 py-1 text-xs font-medium text-blue-700 shadow-sm">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />
              Backend Connected
            </span>
            <span className="inline-flex items-center gap-2 rounded-full border border-blue-100 bg-white/80 px-3 py-1 text-xs font-medium text-blue-700 shadow-sm">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />
              ERPNext Connected
            </span>
          </div>
        </nav>

        <header className="mt-20 text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-blue-600">
            Monthly Payroll Review
          </p>
          <h1 className="mt-5 font-display text-4xl font-semibold tracking-tight text-slate-900 sm:text-5xl lg:text-6xl">
            Payroll Audit Review
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg text-slate-600 sm:text-xl">
            Review payroll anomalies before monthly finalization and ensure payroll accuracy.
          </p>
        </header>

        <section className="mt-14 flex flex-col items-center">
          <div className="w-full max-w-3xl rounded-3xl border border-slate-200/80 bg-white/90 p-8 shadow-[0_24px_60px_-40px_rgba(15,23,42,0.45)] backdrop-blur">
            <div className="flex flex-col gap-6">
              <div className="text-left">
                <h2 className="font-display text-xl font-semibold text-slate-900">
                  Run payroll review
                </h2>
                <p className="mt-2 text-sm text-slate-500">
                  Describe the review focus for this month. You can mention specific teams, periods, or concerns.
                </p>
              </div>
              <textarea
                rows={5}
                className="w-full resize-none rounded-2xl border border-slate-200 bg-slate-50/70 px-5 py-4 text-sm text-slate-700 shadow-inner outline-none transition focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-100"
                placeholder="Compare April vs March payroll, flag unusual salary changes, missing deductions, and new hires."
                value={prompt}
                onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) =>
                  setPrompt(event.target.value)
                }
                disabled={status === 'loading' || isConfirming}
              />
              {inlineValidation && (
                <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
                  <p className="font-semibold">{inlineValidation}</p>
                  <p className="mt-1 text-amber-600">
                    Example: Compare April vs May payroll and flag unusual salary changes.
                  </p>
                </div>
              )}
              <div className="flex flex-wrap gap-2 text-xs text-slate-500">
                {suggestedPrompts.map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setPrompt(item)}
                    className="rounded-full border border-slate-200 bg-white px-3 py-1 transition hover:border-slate-300 hover:bg-slate-50"
                  >
                    {item}
                  </button>
                ))}
              </div>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-slate-400">
                  Estimated review time: under 2 minutes.
                </p>
                <button
                  type="button"
                  onClick={runPayrollReview}
                  disabled={status === 'loading' || isConfirming}
                  className="inline-flex items-center justify-center rounded-full bg-blue-600 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-blue-200/50 transition hover:bg-blue-700 focus:outline-none focus:ring-4 focus:ring-blue-200 disabled:cursor-not-allowed disabled:bg-blue-400"
                >
                  {status === 'loading' ? 'Running Review...' : 'Run Payroll Review'}
                </button>
              </div>
            </div>
          </div>
        </section>

        {status === 'loading' && (
          <section className="mt-12">
            <div className="mx-auto max-w-3xl rounded-3xl border border-slate-200/80 bg-white/90 p-8 text-left shadow-[0_24px_60px_-40px_rgba(15,23,42,0.45)]">
              <p className="text-sm font-semibold text-slate-900">
                Reviewing payroll data
              </p>
              <p className="mt-2 text-sm text-slate-500">
                Comparing month-over-month payroll, checking deductions, and flagging anomalies.
              </p>
              <p className="mt-3 text-xs font-medium uppercase tracking-[0.2em] text-slate-400">
                Current payroll cycle vs previous payroll cycle
              </p>
              <div className="mt-6 grid gap-4 sm:grid-cols-3">
                {['Payroll data', 'Anomaly detection', 'HR-ready summary'].map((step) => (
                  <div
                    key={step}
                    className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs font-medium text-slate-600"
                  >
                    {step}
                  </div>
                ))}
              </div>
              <div className="mt-6 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                <div className="h-full w-2/3 rounded-full bg-blue-500/70" />
              </div>
            </div>
          </section>
        )}

        {status === 'error' && errorMessage && (
          <section className="mt-10">
            <div className="mx-auto max-w-3xl rounded-2xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
              {errorMessage}
            </div>
          </section>
        )}

        {status === 'empty' && (
          <section className="mt-10">
            <div className="mx-auto max-w-3xl rounded-3xl border border-slate-200 bg-white/90 px-6 py-6 text-left text-sm text-slate-600 shadow-sm">
              <p className="text-base font-semibold text-slate-900">
                {emptyMessage ??
                  'No payroll records were found for the selected period.'}
              </p>
              {availablePeriods && availablePeriods.length > 0 && (
                <>
                  <p className="mt-3 text-sm text-slate-500">Available payroll periods:</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {availablePeriods.map((period) => (
                      <span
                        key={period}
                        className="inline-flex items-center rounded-full border border-slate-200 bg-white px-3 py-1 text-[11px] font-medium text-slate-500"
                      >
                        {period}
                      </span>
                    ))}
                  </div>
                </>
              )}
            </div>
          </section>
        )}

        {status === 'awaiting_confirmation' && preview && (
          <section className="mt-16">
            <div className="mx-auto max-w-5xl text-left">
              <div className="mb-10 flex flex-col gap-3">
                <p className="text-xs font-semibold uppercase tracking-[0.3em] text-blue-600">
                  Review ready
                </p>
                <h2 className="font-display text-3xl font-semibold text-slate-900">
                  Payroll review summary
                </h2>
                <p className="max-w-2xl text-base text-slate-500">
                  We have completed the audit and prepared a focused summary for HR review.
                </p>
              </div>

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
                  <p className="text-xs font-medium uppercase tracking-[0.2em] text-slate-400">
                    Employees reviewed
                  </p>
                  <p className="mt-3 text-2xl font-semibold text-slate-900">
                    {preview.employees_in_current_payroll}
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
                  <p className="text-xs font-medium uppercase tracking-[0.2em] text-slate-400">
                    Anomalies found
                  </p>
                  <p className="mt-3 text-2xl font-semibold text-slate-900">
                    {preview.total_anomalies}
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
                  <p className="text-xs font-medium uppercase tracking-[0.2em] text-slate-400">
                    Payroll period
                  </p>
                  <p className="mt-3 text-lg font-semibold text-slate-900">
                    Current payroll cycle vs previous payroll cycle
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
                  <p className="text-xs font-medium uppercase tracking-[0.2em] text-slate-400">
                    Threshold %
                  </p>
                  <p className="mt-3 text-lg font-semibold text-slate-900">Pending</p>
                </div>
              </div>

              <div className="mt-8 rounded-3xl border border-slate-200 bg-white/90 p-5 shadow-sm">
                <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
                  Audit summary
                </p>
                <p className="mt-3 text-sm text-slate-600">
                  {buildPreviewSummary(preview)}
                </p>
              </div>

              <div className="mt-6 flex flex-wrap gap-2">
                <span className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
                  Review generated by
                </span>
                {defaultAgents.map((agent) => (
                  <span
                    key={agent}
                    className="inline-flex items-center rounded-full border border-slate-200 bg-white px-3 py-1 text-[11px] font-medium text-slate-500"
                  >
                    {agent}
                  </span>
                ))}
              </div>

              <div className="mt-10 flex flex-wrap gap-3">
                {Object.entries(preview.breakdown).map(([key, value]) => (
                  <span
                    key={key}
                    className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-600 shadow-sm"
                  >
                    {categoryLabels[key as AnomalyCategory]}
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">
                      {value}
                    </span>
                  </span>
                ))}
              </div>
              <p className="mt-3 text-xs text-slate-400">
                Potential data errors are flagged when unusually large payroll changes lack matching salary revision evidence.
              </p>

              <div className="mt-10 overflow-hidden rounded-3xl border border-slate-200 bg-white/90">
                <div className="border-b border-slate-200 px-6 py-4">
                  <h3 className="text-lg font-semibold text-slate-900">Top anomalies</h3>
                  <p className="mt-1 text-sm text-slate-500">
                    Highest impact changes that may require immediate attention.
                  </p>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-400">
                      <tr>
                        <th className="px-6 py-3">Employee</th>
                        <th className="px-6 py-3">Previous Net Pay</th>
                        <th className="px-6 py-3">Current Net Pay</th>
                        <th className="px-6 py-3">Percentage Change</th>
                        <th className="px-6 py-3">Category</th>
                        <th className="px-6 py-3">Severity</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.top_3_anomalies.length === 0 ? (
                        <tr>
                          <td colSpan={6} className="px-6 py-6 text-sm text-slate-500">
                            No anomalies were detected in this period.
                          </td>
                        </tr>
                      ) : (
                        preview.top_3_anomalies.map((anomaly) => (
                          <tr
                            key={`${anomaly.employee_name}-${anomaly.category}`}
                            className="border-t border-slate-100 transition hover:bg-slate-50/80"
                          >
                            <td className="px-6 py-4 font-medium text-slate-900">
                              {anomaly.employee_name}
                            </td>
                            <td className="px-6 py-4 text-slate-600">
                              {formatCurrency(anomaly.prev_net_pay)}
                            </td>
                            <td className="px-6 py-4 text-slate-600">
                              {formatCurrency(anomaly.curr_net_pay)}
                            </td>
                            <td className="px-6 py-4 text-slate-600">
                              {formatPercent(anomaly.pct_change)}
                            </td>
                            <td className="px-6 py-4 text-slate-600">
                              {categoryLabels[anomaly.category]}
                            </td>
                            <td className="px-6 py-4">
                              <span
                                className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium ${severityStyles[anomaly.severity]}`}
                              >
                                {anomaly.severity}
                              </span>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="mt-12 flex justify-center">
                <div className="w-full max-w-3xl rounded-3xl border border-slate-200 bg-white/90 p-8 text-center shadow-[0_20px_50px_-40px_rgba(15,23,42,0.45)]">
                  <p className="text-base font-semibold text-slate-900">
                    {preview.confirmation_prompt ||
                      `I found ${preview.total_anomalies} anomalies requiring HR review.`}
                  </p>
                  <p className="mt-2 text-sm text-slate-500">
                    Would you like to finalize the payroll audit report?
                  </p>
                  <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-center">
                    <button
                      type="button"
                      disabled={isConfirming}
                      onClick={() => resolveReview(true)}
                      className="inline-flex items-center justify-center rounded-full bg-blue-600 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-blue-200/50 transition hover:bg-blue-700 focus:outline-none focus:ring-4 focus:ring-blue-200 disabled:cursor-not-allowed disabled:bg-blue-400"
                    >
                      {isConfirming ? 'Finalizing...' : 'Confirm Report'}
                    </button>
                    <button
                      type="button"
                      disabled={isConfirming}
                      onClick={() => resolveReview(false)}
                      className="inline-flex items-center justify-center rounded-full border border-slate-200 bg-white px-6 py-3 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 focus:outline-none focus:ring-4 focus:ring-slate-200 disabled:cursor-not-allowed"
                    >
                      Cancel Review
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </section>
        )}

        {status === 'cancelled' && (
          <section className="mt-12">
            <div className="mx-auto max-w-3xl rounded-2xl border border-slate-200 bg-white/90 px-6 py-5 text-center text-sm text-slate-600">
              Payroll review cancelled. No report was finalized.
            </div>
          </section>
        )}

        {status === 'completed' && report && (
          <section className="mt-16">
            <div className="mx-auto max-w-5xl text-left">
              <div className="mb-10 flex flex-col gap-3">
                <p className="text-xs font-semibold uppercase tracking-[0.3em] text-blue-600">
                  Final report
                </p>
                <h2 className="font-display text-3xl font-semibold text-slate-900">
                  Payroll audit report
                </h2>
                <p className="max-w-2xl text-base text-slate-500">{report.summary}</p>
              </div>

              <div className="mb-8 flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
                  Review generated by
                </span>
                {(report.agents_involved ?? defaultAgents).map((agent) => (
                  <span
                    key={agent}
                    className="inline-flex items-center rounded-full border border-slate-200 bg-white px-3 py-1 text-[11px] font-medium text-slate-500"
                  >
                    {agent}
                  </span>
                ))}
              </div>

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
                  <p className="text-xs font-medium uppercase tracking-[0.2em] text-slate-400">
                    Payroll period
                  </p>
                  <p className="mt-3 text-lg font-semibold text-slate-900">
                    {report.period_previous} → {report.period_current}
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
                  <p className="text-xs font-medium uppercase tracking-[0.2em] text-slate-400">
                    Employees evaluated
                  </p>
                  <p className="mt-3 text-2xl font-semibold text-slate-900">
                    {report.employees_evaluated}
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
                  <p className="text-xs font-medium uppercase tracking-[0.2em] text-slate-400">
                    Anomalies found
                  </p>
                  <p className="mt-3 text-2xl font-semibold text-slate-900">
                    {report.total_anomalies}
                  </p>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
                  <p className="text-xs font-medium uppercase tracking-[0.2em] text-slate-400">
                    Threshold %
                  </p>
                  <p className="mt-3 text-lg font-semibold text-slate-900">
                    {report.threshold_pct.toFixed(1)}%
                  </p>
                </div>
              </div>

              <div className="mt-10 space-y-4">
                {report.anomalies.map((anomaly) => (
                  <details
                    key={`${anomaly.employee_name}-${anomaly.anomaly_category}`}
                    className="group rounded-3xl border border-slate-200 bg-white/90 p-6 shadow-sm"
                  >
                    <summary className="flex cursor-pointer list-none flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <p className="text-lg font-semibold text-slate-900">
                          {anomaly.employee_name}
                        </p>
                        <p className="mt-1 text-sm text-slate-500">
                          {categoryLabels[anomaly.anomaly_category]} · {formatPercent(anomaly.pct_change)} change
                        </p>
                      </div>
                      <span
                        className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium ${severityStyles[anomaly.severity]}`}
                      >
                        {anomaly.severity}
                      </span>
                    </summary>
                    <div className="mt-4 grid gap-4 text-sm text-slate-600 sm:grid-cols-2">
                      <div>
                        <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                          Previous net pay
                        </p>
                        <p className="mt-2 text-base font-semibold text-slate-900">
                          {formatCurrency(anomaly.prev_net_pay)}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                          Current net pay
                        </p>
                        <p className="mt-2 text-base font-semibold text-slate-900">
                          {formatCurrency(anomaly.curr_net_pay)}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                          Previous deductions
                        </p>
                        <p className="mt-2 text-base font-semibold text-slate-900">
                          {formatCurrency(anomaly.prev_deductions)}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                          Current deductions
                        </p>
                        <p className="mt-2 text-base font-semibold text-slate-900">
                          {formatCurrency(anomaly.curr_deductions)}
                        </p>
                      </div>
                    </div>
                    {anomaly.missing_deduction_components.length > 0 && (
                      <div className="mt-4 text-sm text-slate-600">
                        <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                          Missing deductions
                        </p>
                        <p className="mt-2">
                          {anomaly.missing_deduction_components.join(', ')}
                        </p>
                      </div>
                    )}
                    <div className="mt-4 rounded-2xl bg-slate-50 p-4 text-sm text-slate-600">
                      <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                        Suggested action
                      </p>
                      <p className="mt-2 text-base font-semibold text-slate-900">
                        {anomaly.suggested_action}
                      </p>
                      {anomaly.llm_explanation && (
                        <p className="mt-2 text-sm text-slate-500">
                          {anomaly.llm_explanation}
                        </p>
                      )}
                    </div>
                  </details>
                ))}
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

export default App
