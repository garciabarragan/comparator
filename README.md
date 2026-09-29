# Comparador de findes

## Instalar
    pip install -r requirements.txt
    playwright install chromium     # solo para el modo captura

## Usar
    python comparador.py                        # BCN ⇄ SVQ, viernes→domingo hasta fin de año
    python comparador.py --sabado --lunes       # más combinaciones
    python comparador.py --umbral 60            # aviso si baja de 60 €

Variables opcionales:
- PROXY_URL=http://user:pass@host:port   (proxy residencial rotativo)
- TELEGRAM_TOKEN / TELEGRAM_CHAT_ID       (avisos al móvil)

## Automatizar (cron, 2 veces al día a horas no redondas)
    17 9  * * * cd ~/comparador && python3 comparador.py --umbral 60
    43 21 * * * cd ~/comparador && python3 comparador.py --umbral 60

## Añadir Vueling, Iryo, Renfe, Ouigo o Alsa
    python comparador.py --capturar vueling
Se abre un navegador limpio; haces la búsqueda a mano, pulsas Enter y se guarda
el tráfico de precios en capturas/vueling.jsonl. Con ese archivo se escribe el
proveedor nuevo (misma interfaz que la clase Ryanair).

## Deploy en GitHub Actions
1. Crea un repo **privado** en GitHub y sube todo el contenido de esta carpeta (incluida `.github/`).
2. En el repo: Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_TOKEN`: token del bot (créalo hablando con @BotFather en Telegram)
   - `TELEGRAM_CHAT_ID`: tu chat id (escribe a tu bot y abre https://api.telegram.org/bot<TOKEN>/getUpdates)
   - `PROXY_URL` (opcional pero recomendado)
3. Pestaña Actions → "Comparador findes" → Run workflow, para probarlo.
Se ejecuta solo 2 veces al día; el último ranking queda en `ultimo_resultado.txt`.
Para cambiar ruta o umbral, edita la línea `run: python comparador.py ...` del workflow.
