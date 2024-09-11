#!/bin/bash

# Definir los tiles a descargar
TILES=("30STG" "30SUG" "30SUF" "30STF")

# Definir el año de inicio y fin (en orden inverso)
START_YEAR=2024
END_YEAR=2018

# Recorrer los años en orden inverso
for YEAR in $(seq $START_YEAR -1 $END_YEAR); do
    
    # Recorrer los meses de 1 en 1
    for MONTH in {01..12}; do

        # Recorrer los tiles
        for TILE in "${TILES[@]}"; do
            # Definir la fecha de inicio en formato aaaa-mm-dd
            FROM_DATE="${YEAR}-${MONTH}-01"

            # Calcular el mes siguiente
            NEXT_MONTH=$((10#$MONTH + 1))
            NEXT_YEAR=$YEAR
            
            # Si el próximo mes es mayor que 12, avanzar al próximo año y ajustar el mes
            if [ $NEXT_MONTH -gt 12 ]; then
                NEXT_MONTH=01
                NEXT_YEAR=$((YEAR + 1))
            fi
            
            # Definir la fecha de fin en formato aaaa-mm-dd
            TO_DATE=$(date -d "$NEXT_YEAR-$(printf "%02d" $NEXT_MONTH)-01 -1 day" +%Y-%m-%d)
            
            # Llamar al script de Python con los argumentos correspondientes
            python simple_script.py --tile "$TILE" --from-date "$FROM_DATE" --to-date "$TO_DATE"
            exit
        done
    done
done
