import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { Layout } from './Layout'
import { Dashboard } from './pages/Dashboard'
import { ApplicationDetail } from './pages/ApplicationDetail'
import { Reminders } from './pages/Reminders'
import { Analytics } from './pages/Analytics'
import { ToastProvider } from './lib/toast'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Layout>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/reminders" element={<Reminders />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/applications/:id" element={<ApplicationDetail />} />
            </Routes>
          </Layout>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
)
