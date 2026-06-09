/**
 * WsContext — shares the single WebSocket connection from Layout
 * with any page that wants to react to real-time pipeline events.
 *
 * Usage in a page:
 *   import { useWsEvent } from '../WsContext.jsx'
 *   useWsEvent(msg => { if (msg.type === 'job_update') refetch() })
 */
import { createContext, useContext, useEffect, useRef } from 'react'

// Value: a React ref whose .current is a Set of handler functions.
export const WsContext = createContext(null)

/**
 * Subscribe to WebSocket messages.  handler is called with the parsed
 * message object every time a message arrives.  Always uses the latest
 * version of the callback without re-registering the listener.
 */
export function useWsEvent(handler) {
  const handlersRef = useContext(WsContext)

  // Keep a stable wrapper around the (potentially-changing) handler
  const handlerRef = useRef(handler)
  handlerRef.current = handler

  useEffect(() => {
    if (!handlersRef) return
    const stable = msg => handlerRef.current(msg)
    handlersRef.current.add(stable)
    return () => { handlersRef.current.delete(stable) }
  }, [handlersRef]) // only once per mount
}
