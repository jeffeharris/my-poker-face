import { useState, useEffect, useRef } from 'react'
import toast from 'react-hot-toast'

/**
 * Hook that monitors navigator.onLine status and shows toast notifications
 * when the connection is lost or restored.
 */
export function useOnlineStatus(): boolean {
  const [isOnline, setIsOnline] = useState<boolean>(navigator.onLine)
  const offlineToastId = useRef<string | null>(null)

  useEffect(() => {
    const handleOffline = () => {
      setIsOnline(false)
      offlineToastId.current = toast.error('Connection lost. Some features may not work.', {
        duration: Infinity,
        id: 'offline-status',
      })
    }

    const handleOnline = () => {
      setIsOnline(true)
      // Dismiss the offline toast
      if (offlineToastId.current) {
        toast.dismiss(offlineToastId.current)
        offlineToastId.current = null
      }
      toast.success('Back online', { duration: 3000, id: 'online-status' })
    }

    window.addEventListener('offline', handleOffline)
    window.addEventListener('online', handleOnline)

    return () => {
      window.removeEventListener('offline', handleOffline)
      window.removeEventListener('online', handleOnline)
      // Clean up any lingering toast on unmount
      if (offlineToastId.current) {
        toast.dismiss(offlineToastId.current)
      }
    }
  }, [])

  return isOnline
}
