#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Import gazu do Polski według kraju pochodzenia, API DBW GUS.

URUCHOMIENIE:
    python gus_gaz_api.py

Opcjonalnie:
    python gus_gaz_api.py --od 2018 --do 2020
    python gus_gaz_api.py --odswiez

WYNIKI w folderze gus_gaz:
    gus_gaz_miesieczne.csv
    gus_gaz_roczne.csv
    raport_pobierania.csv
    metadane_gus.json

CSV:
    separator ;
    przecinek dziesiętny
    UTF-8 z BOM

Zmienna 221:
    Import towarów wg kraju pochodzenia.

Przekrój 1434:
    Polska; Kraje towary; CN - uzupełniająca jednostka miary.

CN 27111100:
    gaz ziemny skroplony

CN 27112100:
    gaz ziemny w stanie gazowym.

API nie dokumentuje filtra CN dla endpointu danych.
Trzeba pobrać wszystkie strony danego okresu i dopiero wybrać dwa kody.

Postęp jest zapisywany po każdej stronie w folderze cache.
Po przerwaniu można uruchomić program ponownie.

Źródło:
Główny Urząd Statystyczny, Dziedzinowe Bazy Wiedzy, CC BY 4.0.
"""

import argparse
import calendar
import csv
import getpass
import gzip
import json
import os
import re
import sys
import time

from datetime import date, datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# ============================================================
# USTAWIENIA
# ============================================================

API = "https://api-dbw.stat.gov.pl/api/1.2.0/"

ID_ZMIENNEJ = 221
ID_PRZEKROJU = 1434

KODY_CN = {
    "27111100",
    "27112100",
}

ROK_OD = 2018

# None = do najnowszego dostępnego roku
ROK_DO = None


# ============================================================
# KLUCZ API
# ============================================================

KLUCZ_API = "rd0rSweA0HdXUfrNpJB5U6vypciTFcvBzuM8kRFarVU="


# ============================================================
# POZOSTAŁE USTAWIENIA
# ============================================================

ROZMIAR_STRONY = 5000

KATEGORIE_SPECJALNE = {
    "QP",
    "QQ",
    "QR",
    "QS",
    "QU",
    "QV",
    "QW",
    "QX",
    "QY",
    "QZ",
    "_no",
}


KOLUMNY = [
    "okres",
    "rok",
    "miesiac",
    "kraj",
    "kod_kraju",
    "typ_kraju",
    "kod_cn",
    "towar",
    "wartosc",
    "jednostka",
    "status",
    "flaga_gus",
    "tajnosc_gus",
    "brak_wartosci_gus",
    "wartosc_opisowa_gus",
    "precyzja_gus",
    "id_kraju_gus",
    "id_cn_gus",
    "id_okresu_gus",
    "data_pobrania_utc",
]


# ============================================================
# ZAPIS PLIKÓW
# ============================================================

def zapisz_json(sciezka, dane):

    tymczasowy = sciezka.with_suffix(
        sciezka.suffix + ".tmp"
    )

    tymczasowy.write_text(
        json.dumps(
            dane,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    tymczasowy.replace(sciezka)


def zapisz_csv(sciezka, kolumny, wiersze):

    tymczasowy = sciezka.with_suffix(
        ".csv.tmp"
    )

    with tymczasowy.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=kolumny,
            delimiter=";"
        )

        writer.writeheader()
        writer.writerows(wiersze)

    tymczasowy.replace(sciezka)


# ============================================================
# KLIENT API GUS
# ============================================================

class KlientGUS:

    def __init__(self, klucz):

        self.klucz = klucz
        self.ostatnie_zadanie = 0.0


    def pobierz(
        self,
        endpoint,
        parametry=None,
        dopuszczaj_404=False
    ):

        url = (
            API
            + endpoint
            + "?"
            + urlencode(
                {
                    "lang": "pl",
                    **(parametry or {})
                }
            )
        )

        for proba in range(6):

            # Około 439 żądań / 15 min.
            # Limit GUS dla klucza API to 500 / 15 min.
            time.sleep(
                max(
                    0,
                    2.05
                    - (
                        time.monotonic()
                        - self.ostatnie_zadanie
                    )
                )
            )

            self.ostatnie_zadanie = time.monotonic()

            naglowki = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-ClientId": self.klucz,
                "User-Agent": "GUS-DBW-gaz/1.0",
            }

            opoznienie = min(
                60,
                5 * 2 ** proba
            )

            try:

                request = Request(
                    url,
                    headers=naglowki
                )

                with urlopen(
                    request,
                    timeout=90
                ) as response:

                    tresc = response.read()

                    if (
                        response.headers.get(
                            "Content-Encoding"
                        )
                        == "gzip"
                    ):
                        tresc = gzip.decompress(
                            tresc
                        )

                    return json.loads(
                        tresc.decode(
                            "utf-8-sig"
                        )
                    )

            except HTTPError as e:

                if (
                    e.code == 404
                    and dopuszczaj_404
                ):
                    return None

                if e.code in (401, 403):

                    raise RuntimeError(
                        "GUS odrzucił klucz API "
                        "(HTTP 401/403). "
                        "Sprawdź klucz."
                    ) from None

                if e.code not in (
                    408,
                    429,
                    500,
                    502,
                    503,
                    504,
                ):

                    raise RuntimeError(
                        f"Błąd HTTP {e.code}: "
                        f"{endpoint}"
                    ) from None

                if e.code == 429:

                    opoznienie = 120

                    retry = e.headers.get(
                        "Retry-After",
                        ""
                    )

                    if retry.isdigit():

                        opoznienie = max(
                            1,
                            int(retry)
                        )

                    elif retry:

                        try:

                            opoznienie = max(
                                1,
                                (
                                    parsedate_to_datetime(
                                        retry
                                    )
                                    - datetime.now(
                                        timezone.utc
                                    )
                                ).total_seconds()
                            )

                        except (
                            ValueError,
                            TypeError
                        ):
                            pass

                powod = f"HTTP {e.code}"

            except (
                URLError,
                TimeoutError,
                OSError,
                ValueError
            ) as e:

                powod = type(e).__name__

            if proba == 5:

                raise RuntimeError(
                    f"Nie udało się pobrać "
                    f"{endpoint}: {powod}. "
                    "Postęp zachowano; "
                    "uruchom program ponownie."
                )

            print(
                f"  {powod}; "
                f"ponawiam za "
                f"{opoznienie:.0f} s.",
                flush=True
            )

            time.sleep(opoznienie)


    def slownik(
        self,
        nazwa,
        klucz
    ):

        wynik = {}

        strona = 1

        while True:

            dane = self.pobierz(
                "dictionaries/" + nazwa,
                {
                    "page": strona,
                    "page-size": ROZMIAR_STRONY
                }
            )

            if (
                not isinstance(
                    dane,
                    dict
                )
                or not isinstance(
                    dane.get("data"),
                    list
                )
            ):

                raise RuntimeError(
                    "Nieoczekiwana odpowiedź "
                    f"słownika: {nazwa}"
                )

            wynik.update(
                {
                    x[klucz]: x
                    for x in dane["data"]
                }
            )

            if (
                strona
                >= dane["page-count"]
            ):

                return wynik

            strona += 1


# ============================================================
# METADANE
# ============================================================

def wczytaj_metadane(klient):

    meta = klient.pobierz(
        "variable/variable-meta",
        {
            "id-zmiennej":
                ID_ZMIENNEJ
        }
    )

    if (
        meta.get("nazwa")
        != "Import towarów wg kraju pochodzenia"
    ):

        raise RuntimeError(
            "Nazwa zmiennej GUS "
            "zmieniła się. "
            "Sprawdź metadane."
        )


    pozycje = klient.pobierz(
        "variable/variable-section-position",
        {
            "id-przekroj":
                ID_PRZEKROJU
        }
    )


    # --------------------------------------------------------
    # KRAJE
    # --------------------------------------------------------

    kraje = {
        x["id-pozycja"]: x

        for x in pozycje

        if (
            x["nazwa-wymiar"]
            == "Kraje towary"

            and x.get("symbol")
            not in (
                "00",
                "EU"
            )

            and (
                x["nazwa-pozycja"]
                .strip()
                .casefold()
                != "ogółem"
            )
        )
    }


    # --------------------------------------------------------
    # TOWARY CN
    # --------------------------------------------------------

    towary = {
        x["id-pozycja"]:
            dict(x)

        for x in pozycje

        if (
            x["nazwa-wymiar"]
            == (
                "CN - uzupełniająca "
                "jednostka miary"
            )

            and x.get("symbol")
            in KODY_CN
        )
    }


    if (
        {
            x["symbol"]
            for x in towary.values()
        }
        != KODY_CN
        or not kraje
    ):

        raise RuntimeError(
            "Nie znaleziono obu "
            "kodów CN lub "
            "słownika krajów."
        )


    # --------------------------------------------------------
    # JEDNOSTKI
    # --------------------------------------------------------

    for x in towary.values():

        jednostka = re.search(
            r"\[([^\[\]]+)\]\s*$",
            x["nazwa-pozycja"]
        )

        if not jednostka:

            raise RuntimeError(
                "Brak jednostki "
                "w opisie CN "
                f"{x['symbol']}."
            )

        x["jednostka"] = (
            jednostka.group(1)
        )


    # --------------------------------------------------------
    # OKRESY
    # --------------------------------------------------------

    okresy = klient.slownik(
        "periods-dictionary",
        "id-okres"
    )

    miesiace = {}

    for ident, x in okresy.items():

        m = re.fullmatch(
            r"M(\d{2})",
            x.get(
                "symbol",
                ""
            )
        )

        if (
            x["id-czestotliwosc"]
            == 3

            and x["id-typ"]
            == 1

            and m
        ):

            miesiace[
                int(
                    m.group(1)
                )
            ] = ident


    roczne = [

        i

        for i, x
        in okresy.items()

        if (
            x["opis"]
            == (
                "rok - dane roczne "
                "- rok"
            )
        )
    ]


    if (
        set(miesiace)
        != set(
            range(
                1,
                13
            )
        )

        or len(roczne)
        != 1
    ):

        raise RuntimeError(
            "Nie udało się "
            "jednoznacznie "
            "rozpoznać okresów GUS."
        )


    # --------------------------------------------------------
    # SŁOWNIKI
    # --------------------------------------------------------

    flagi = klient.slownik(
        "flag-dictionary",
        "id-flaga"
    )

    braki = klient.slownik(
        "no-value-dictionary",
        "id-brak-wartosci"
    )

    tajnosc = klient.slownik(
        "confidentionality-dictionary",
        "id-tajnosci"
    )


    # --------------------------------------------------------
    # SERIE
    # --------------------------------------------------------

    serie = {

        x["id-czestotliwosc"]: x

        for x
        in meta["przekroje"]

        if (
            x["id-przekroj"]
            == ID_PRZEKROJU
        )
    }


    if not {
        1,
        3
    }.issubset(
        serie
    ):

        raise RuntimeError(
            "Brak miesięcznej "
            "lub rocznej serii "
            "dla tego przekroju."
        )


    return {

        "zrodlo":
            "GUS, DBW, CC BY 4.0",

        "api":
            API,

        "zmienna":
            meta,

        "kraje":
            kraje,

        "towary":
            towary,

        "miesiace":
            miesiace,

        "okres_roczny":
            roczne[0],

        "serie":
            serie,

        "flagi":
            flagi,

        "braki":
            braki,

        "tajnosc":
            tajnosc,
    }


# ============================================================
# ODCZYT WYMIARU Z REKORDU GUS
# ============================================================

def pozycja_wymiaru(
    wiersz,
    wymiar
):

    for i in range(
        1,
        16
    ):

        if (
            wiersz.get(
                f"id-wymiar-{i}"
            )
            == wymiar
        ):

            return wiersz[
                f"id-pozycja-{i}"
            ]

    raise RuntimeError(
        f"Brakuje wymiaru "
        f"{wymiar} "
        "w danych GUS."
    )


# ============================================================
# POBIERANIE JEDNEGO OKRESU
# ============================================================

def pobierz_okres(
    klient,
    meta,
    rok,
    miesiac,
    cache,
    odswiez=False
):

    if miesiac:

        okres = (
            meta["miesiace"][
                miesiac
            ]
        )

    else:

        okres = (
            meta[
                "okres_roczny"
            ]
        )


    if miesiac:

        etykieta = (
            f"{rok} "
            f"M{miesiac}"
        )

    else:

        etykieta = str(
            rok
        )


    aktualizacja = (
        meta["serie"][
            3 if miesiac else 1
        ][
            "aktualizacja-ostatnia"
        ]
    )


    plik = (
        cache
        / f"{rok}_{okres}.json"
    )


    sygnatura = [
        2,
        API,
        ID_ZMIENNEJ,
        ID_PRZEKROJU,
        sorted(
            KODY_CN
        ),
        ROZMIAR_STRONY,
        aktualizacja,
    ]


    stan = {

        "sygnatura":
            sygnatura,

        "rok":
            rok,

        "miesiac":
            miesiac,

        "id_okres":
            okres,

        "okres":
            etykieta,

        "nastepna_strona":
            0,

        "liczba_stron":
            0,

        "ostatnia_strona_api":
            None,

        "wiersze_api":
            0,

        "dane":
            [],

        "gotowe":
            False,
    }


    # --------------------------------------------------------
    # WCZYTANIE CACHE
    # --------------------------------------------------------

    if (
        plik.exists()
        and not odswiez
    ):

        zapisany = (
            json.loads(
                plik.read_text(
                    encoding="utf-8"
                )
            )
        )

        if (
            zapisany.get(
                "sygnatura"
            )
            == sygnatura
        ):

            stan = zapisany


    if stan["gotowe"]:

        print(
            f"{etykieta}: "
            "z zapisanego pliku.",
            flush=True
        )

        return stan


    wymiar_cn = next(
        iter(
            meta[
                "towary"
            ].values()
        )
    )[
        "id-wymiar"
    ]


    wymiar_kraju = next(
        iter(
            meta[
                "kraje"
            ].values()
        )
    )[
        "id-wymiar"
    ]


    wymiar_polski = 2


    # --------------------------------------------------------
    # KOLEJNE STRONY API
    # --------------------------------------------------------

    while not stan[
        "gotowe"
    ]:

        strona = (
            stan[
                "nastepna_strona"
            ]
        )


        parametry = {

            "id-zmienna":
                ID_ZMIENNEJ,

            "id-przekroj":
                ID_PRZEKROJU,

            "id-rok":
                rok,

            "id-okres":
                okres,

            "ile-na-stronie":
                ROZMIAR_STRONY,

            "numer-strony":
                strona,
        }


        d = klient.pobierz(
            "variable/"
            "variable-data-section",
            parametry,
            dopuszczaj_404=(
                strona == 0
            )
        )


        if d is None:

            print(
                f"{etykieta}: "
                "GUS nie udostępnia "
                "tego okresu "
                "(HTTP 404).",
                flush=True
            )

            return None


        if (
            not isinstance(
                d,
                dict
            )

            or (
                d.get(
                    "page-number"
                )
                != strona
            )

            or not isinstance(
                d.get(
                    "data"
                ),
                list
            )
        ):

            raise RuntimeError(
                "Nieprawidłowe "
                "stronicowanie dla "
                f"{etykieta}."
            )


        if (
            not d["data"]
            and strona == 0
            and d.get(
                "page-count",
                0
            )
            == 0
        ):

            return None


        # UWAGA:
        # W tym endpoincie page-count
        # jest indeksem ostatniej strony.
        #
        # page-count = 5
        # oznacza strony:
        # 0,1,2,3,4,5

        ostatnia_strona = (
            d[
                "page-count"
            ]
        )


        if (
            stan[
                "ostatnia_strona_api"
            ]
            not in (
                None,
                ostatnia_strona
            )
        ):

            raise RuntimeError(
                "GUS zmienił dane "
                "w trakcie pobierania "
                f"{etykieta}. "
                "Usuń plik cache "
                f"{plik.name} "
                "i uruchom ponownie."
            )


        if (
            strona
            > ostatnia_strona

            or (
                not d["data"]
                and strona
                != ostatnia_strona
            )
        ):

            raise RuntimeError(
                "Pusta lub "
                "nieprawidłowa strona "
                f"dla {etykieta}."
            )


        oczekiwany = (
            strona
            * ROZMIAR_STRONY
            + 1
        )


        for offset, w in enumerate(
            d["data"]
        ):

            if (
                w.get(
                    "rownumber"
                )
                != (
                    oczekiwany
                    + offset
                )

                or (
                    w[
                        "id-zmienna"
                    ]
                    != ID_ZMIENNEJ
                )

                or (
                    w[
                        "id-przekroj"
                    ]
                    != ID_PRZEKROJU
                )

                or (
                    w[
                        "id-daty"
                    ]
                    != rok
                )

                or (
                    w[
                        "id-okres"
                    ]
                    != okres
                )
            ):

                raise RuntimeError(
                    "Niespójne rekordy "
                    "API dla "
                    f"{etykieta}."
                )


            cn = pozycja_wymiaru(
                w,
                wymiar_cn
            )


            if (
                cn
                not in meta[
                    "towary"
                ]
            ):
                continue


            kraj = pozycja_wymiaru(
                w,
                wymiar_kraju
            )


            if (
                kraj
                in meta[
                    "kraje"
                ]
            ):

                if (
                    pozycja_wymiaru(
                        w,
                        wymiar_polski
                    )
                    != 33617
                ):

                    raise RuntimeError(
                        "Dane nie dotyczą "
                        "Polski."
                    )


                stan[
                    "dane"
                ].append(
                    {
                        **w,
                        "kraj_id":
                            kraj,
                        "cn_id":
                            cn,
                    }
                )


        if (
            strona
            < ostatnia_strona

            and len(
                d["data"]
            )
            != ROZMIAR_STRONY
        ):

            raise RuntimeError(
                "Niepełna "
                "pośrednia strona "
                f"dla {etykieta}."
            )


        # ----------------------------------------------------
        # ZAPIS POSTĘPU PO KAŻDEJ STRONIE
        # ----------------------------------------------------

        stan.update(

            nastepna_strona=(
                strona + 1
            ),

            liczba_stron=(
                strona + 1
            ),

            ostatnia_strona_api=(
                ostatnia_strona
            ),

            wiersze_api=(
                stan[
                    "wiersze_api"
                ]
                + len(
                    d["data"]
                )
            ),

            gotowe=(
                strona
                == ostatnia_strona
            ),

            data_pobrania=(
                datetime.now(
                    timezone.utc
                ).isoformat(
                    timespec="seconds"
                )
            ),
        )


        zapisz_json(
            plik,
            stan
        )


        print(

            f"{etykieta}: "

            f"strona "
            f"{strona + 1}/"
            f"{ostatnia_strona + 1}, "

            "rekordów dla gazu: "
            f"{len(stan['dane'])}.",

            flush=True
        )


    return stan


# ============================================================
# SPRAWDZENIE AKTYWNOŚCI POZYCJI
# ============================================================

def aktywna(
    pozycja,
    rok,
    miesiac
):

    poczatek = date(
        rok,
        miesiac or 1,
        1
    ).isoformat()


    koniec = date(

        rok,

        miesiac or 12,

        calendar.monthrange(
            rok,
            miesiac or 12
        )[1]

    ).isoformat()


    return (

        (
            pozycja.get(
                "data-poczatku"
            )
            or "0001-01-01"
        )[:10]
        <= koniec

        and

        (
            pozycja.get(
                "data-konca"
            )
            or "9999-12-31"
        )[:10]
        >= poczatek
    )


# ============================================================
# OPIS SŁOWNIKA
# ============================================================

def opis(
    slownik,
    ident
):

    if ident is None:
        return ""


    if ident not in slownik:

        raise RuntimeError(
            "Nieznany "
            "identyfikator "
            "w słowniku GUS: "
            f"{ident}"
        )


    return (
        slownik[
            ident
        ][
            "nazwa"
        ]
    )


# ============================================================
# GENEROWANIE WIERSZY CSV
# ============================================================

def wiersze_csv(
    meta,
    stany,
    roczne
):

    kraje = sorted(

        meta[
            "kraje"
        ].items(),

        key=lambda kv:
            kv[1][
                "nazwa-pozycja"
            ]
    )


    towary = sorted(

        meta[
            "towary"
        ].items(),

        key=lambda kv:
            kv[1][
                "symbol"
            ]
    )


    for stan in stany:

        if (
            (
                stan[
                    "miesiac"
                ]
                == 0
            )
            != roczne
        ):
            continue


        rekordy = {}


        for w in stan[
            "dane"
        ]:

            klucz = (
                w[
                    "kraj_id"
                ],
                w[
                    "cn_id"
                ]
            )


            if klucz in rekordy:

                raise RuntimeError(
                    "Duplikat kraj/CN "
                    f"dla "
                    f"{stan['okres']}: "
                    f"{klucz}"
                )


            if (
                w[
                    "id-sposob-prezentacji-miara"
                ]
                != 117
            ):

                raise RuntimeError(
                    "Nieoczekiwany "
                    "typ informacji; "
                    "sprawdź przekrój GUS."
                )


            rekordy[
                klucz
            ] = w


        # ----------------------------------------------------
        # KAŻDY KRAJ × KAŻDY CN
        # ----------------------------------------------------

        for (
            kraj_id,
            kraj
        ) in kraje:

            if not aktywna(
                kraj,
                stan[
                    "rok"
                ],
                stan[
                    "miesiac"
                ]
            ):
                continue


            for (
                cn_id,
                cn
            ) in towary:

                if not aktywna(
                    cn,
                    stan[
                        "rok"
                    ],
                    stan[
                        "miesiac"
                    ]
                ):
                    continue


                w = rekordy.get(
                    (
                        kraj_id,
                        cn_id
                    ),
                    {}
                )


                wartosc = w.get(
                    "wartosc"
                )


                if wartosc is not None:

                    status = (
                        "wartosc_opublikowana"
                    )

                else:

                    status = (
                        "brak_wartosci_w_api"
                    )


                if not w:

                    status = (
                        "brak_rekordu_w_api"
                    )


                elif (
                    meta[
                        "tajnosc"
                    ].get(
                        w.get(
                            "id-tajnosci"
                        ),
                        {}
                    ).get(
                        "oznaczenie"
                    )
                    == "(:)"
                ):

                    status = (
                        "tajemnica_statystyczna"
                    )

                    wartosc = None


                elif (
                    wartosc is None

                    and w.get(
                        "id-brak-wartosci"
                    )
                    == 42
                ):

                    status = (
                        "zjawisko_nie_wystapilo"
                    )


                yield {

                    "okres":
                        stan[
                            "okres"
                        ],

                    "rok":
                        stan[
                            "rok"
                        ],

                    "miesiac":
                        (
                            stan[
                                "miesiac"
                            ]
                            or ""
                        ),

                    "kraj":
                        kraj[
                            "nazwa-pozycja"
                        ],

                    "kod_kraju":
                        kraj.get(
                            "symbol",
                            ""
                        ),

                    "typ_kraju":
                        (
                            "kategoria_specjalna"

                            if (
                                kraj.get(
                                    "symbol"
                                )
                                in KATEGORIE_SPECJALNE
                            )

                            else
                            "kraj_lub_terytorium"
                        ),

                    "kod_cn":
                        cn[
                            "symbol"
                        ],

                    "towar":
                        cn[
                            "nazwa-pozycja"
                        ],

                    "wartosc":
                        (
                            ""

                            if wartosc is None

                            else format(
                                Decimal(
                                    str(
                                        wartosc
                                    )
                                ),
                                "f"
                            ).replace(
                                ".",
                                ","
                            )
                        ),

                    "jednostka":
                        cn[
                            "jednostka"
                        ],

                    "status":
                        status,

                    "flaga_gus":
                        opis(
                            meta[
                                "flagi"
                            ],
                            w.get(
                                "id-flaga"
                            )
                        ),

                    "tajnosc_gus":
                        opis(
                            meta[
                                "tajnosc"
                            ],
                            w.get(
                                "id-tajnosci"
                            )
                        ),

                    "brak_wartosci_gus":
                        opis(
                            meta[
                                "braki"
                            ],
                            w.get(
                                "id-brak-wartosci"
                            )
                        ),

                    "wartosc_opisowa_gus":
                        w.get(
                            "wartosc-opisowa",
                            ""
                        ),

                    "precyzja_gus":
                        w.get(
                            "precyzja",
                            ""
                        ),

                    "id_kraju_gus":
                        kraj_id,

                    "id_cn_gus":
                        cn_id,

                    "id_okresu_gus":
                        stan[
                            "id_okres"
                        ],

                    "data_pobrania_utc":
                        stan[
                            "data_pobrania"
                        ],
                }


# ============================================================
# EKSPORT
# ============================================================

def eksportuj(
    folder,
    meta,
    stany,
    raport
):

    for (
        roczne,
        nazwa
    ) in [

        (
            False,
            "gus_gaz_miesieczne.csv"
        ),

        (
            True,
            "gus_gaz_roczne.csv"
        ),
    ]:

        zapisz_csv(

            folder / nazwa,

            KOLUMNY,

            wiersze_csv(
                meta,
                stany,
                roczne
            )
        )


    zapisz_csv(

        folder
        / "raport_pobierania.csv",

        [
            "okres",
            "typ_okresu",
            "status",
            "strony",
            "rekordy_gazu",
            "uwagi"
        ],

        raport
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(

        description=__doc__,

        formatter_class=(
            argparse.RawDescriptionHelpFormatter
        )
    )


    parser.add_argument(

        "--od",

        type=int,

        default=ROK_OD,

        dest="rok_od"
    )


    parser.add_argument(

        "--do",

        type=int,

        default=(
            ROK_DO
            or date.today().year
        ),

        dest="rok_do"
    )


    # --------------------------------------------------------
    # Folder wynikowy
    # --------------------------------------------------------

    if "__file__" in globals():

        katalog_bazowy = (
            Path(
                __file__
            )
            .resolve()
            .parent
        )

    else:

        katalog_bazowy = (
            Path.cwd()
        )


    katalog_domyslny = (
        katalog_bazowy
        / "gus_gaz"
    )


    parser.add_argument(

        "--katalog",

        type=Path,

        default=(
            katalog_domyslny
        )
    )


    parser.add_argument(

        "--odswiez",

        action="store_true",

        help=(
            "Pobierz ponownie "
            "także zapisane okresy."
        )
    )


    # --------------------------------------------------------
    # JUPYTER / NORMALNY PYTHON
    # --------------------------------------------------------

    if "ipykernel" in sys.modules:

        args, _nieznane = (
            parser.parse_known_args()
        )

    else:

        args = (
            parser.parse_args()
        )


    # --------------------------------------------------------
    # WALIDACJA LAT
    # --------------------------------------------------------

    if not (
        2004
        <= args.rok_od
        <= args.rok_do
        <= date.today().year
    ):

        parser.error(
            "Zakres lat musi "
            "mieścić się między "
            "2004 a bieżącym rokiem."
        )


    # --------------------------------------------------------
    # KLUCZ API
    # --------------------------------------------------------

    klucz = (

        os.environ.get(
            "GUS_DBW_API_KEY",
            ""
        ).strip()

        or

        KLUCZ_API.strip()
    )


    if not klucz:

        klucz = getpass.getpass(
            "Wklej klucz API DBW "
            "i naciśnij Enter "
            "(tekst będzie niewidoczny): "
        ).strip()


    if not klucz:

        raise RuntimeError(
            "Nie podano "
            "klucza API DBW."
        )


    klient = KlientGUS(
        klucz
    )


    # --------------------------------------------------------
    # FOLDERY
    # --------------------------------------------------------

    folder = (
        args.katalog
        .resolve()
    )


    cache = (
        folder
        / "cache"
    )


    cache.mkdir(
        parents=True,
        exist_ok=True
    )


    # --------------------------------------------------------
    # METADANE
    # --------------------------------------------------------

    print(
        "Pobieram słowniki GUS "
        "i sprawdzam zakres danych...",
        flush=True
    )


    meta = (
        wczytaj_metadane(
            klient
        )
    )


    zapisz_json(
        folder
        / "metadane_gus.json",
        meta
    )


    for cn in sorted(

        meta[
            "towary"
        ].values(),

        key=lambda x:
            x["symbol"]
    ):

        print(
            f"  "
            f"{cn['nazwa-pozycja']}"
        )


    print(
        "Import według kraju "
        "pochodzenia. "
        "Kraje osobno, "
        "bez Ogółem i agregatu UE."
    )


    # --------------------------------------------------------
    # LISTA ZADAŃ
    # --------------------------------------------------------

    stany = []
    raport = []

    dzis = date.today()

    zadania = []


    for rok in range(
        args.rok_od,
        args.rok_do + 1
    ):

        for miesiac in (
            list(
                range(
                    1,
                    13
                )
            )
            + [0]
        ):

            # Bieżący miesiąc i bieżący
            # rok roczny pomijamy.

            if (
                rok
                == dzis.year

                and (
                    miesiac == 0
                    or miesiac
                    >= dzis.month
                )
            ):

                continue


            seria = (
                meta[
                    "serie"
                ][
                    3
                    if miesiac
                    else 1
                ]
            )


            lata = [

                int(x)

                for x
                in re.findall(
                    r"\d{4}",
                    seria[
                        "szereg-czasowy"
                    ]
                )
            ]


            if not lata:

                raise RuntimeError(
                    "Nie rozpoznano "
                    "zakresu lat "
                    "w metadanych GUS."
                )


            if (
                min(lata)
                <= rok
                <= max(lata)
            ):

                zadania.append(
                    (
                        rok,
                        miesiac
                    )
                )


    # --------------------------------------------------------
    # POBIERANIE
    # --------------------------------------------------------

    try:

        for (
            indeks,
            (
                rok,
                miesiac
            )
        ) in enumerate(
            zadania
        ):

            wpis = {

                "okres":
                    (
                        f"{rok} "
                        f"M{miesiac}"
                        if miesiac
                        else str(rok)
                    ),

                "typ_okresu":
                    (
                        "miesieczny"
                        if miesiac
                        else "roczny"
                    ),

                "status":
                    "w_trakcie",

                "strony":
                    "",

                "rekordy_gazu":
                    "",

                "uwagi":
                    "",
            }


            raport.append(
                wpis
            )


            try:

                stan = pobierz_okres(

                    klient,

                    meta,

                    rok,

                    miesiac,

                    cache,

                    args.odswiez
                )


            except (
                RuntimeError,
                OSError,
                ValueError,
                KeyboardInterrupt
            ):

                wpis[
                    "status"
                ] = (
                    "przerwano_lub_blad"
                )

                wpis[
                    "uwagi"
                ] = (
                    "Okres nie jest "
                    "kompletny i nie "
                    "trafił do CSV. "
                    "Uruchom ponownie."
                )

                raise


            if stan is None:

                wpis[
                    "status"
                ] = (
                    "brak_okresu_w_api"
                )

                wpis[
                    "uwagi"
                ] = (
                    "Brak opublikowanej "
                    "tabeli. "
                    "To nie oznacza "
                    "zerowego importu."
                )


            else:

                stany.append(
                    stan
                )

                wpis.update(

                    status="pobrano",

                    strony=(
                        stan[
                            "liczba_stron"
                        ]
                    ),

                    rekordy_gazu=(
                        len(
                            stan[
                                "dane"
                            ]
                        )
                    )
                )


            # Eksport po każdym zakończonym roku.

            if (
                indeks
                == len(
                    zadania
                )
                - 1

                or (
                    zadania[
                        indeks + 1
                    ][0]
                    != rok
                )
            ):

                eksportuj(
                    folder,
                    meta,
                    stany,
                    raport
                )


    finally:

        # Po przerwaniu zapisujemy
        # wszystko, co zostało
        # pobrane kompletnie.

        if raport:

            eksportuj(
                folder,
                meta,
                stany,
                raport
            )


    # --------------------------------------------------------
    # PODSUMOWANIE
    # --------------------------------------------------------

    if not stany:

        raise RuntimeError(
            "Nie pobrano żadnego "
            "kompletnego okresu. "
            "Sprawdź "
            "raport_pobierania.csv."
        )


    brakujace = sum(

        x["status"]
        != "pobrano"

        for x in raport
    )


    print(
        "\nZapisano "
        f"{len(stany)} "
        "kompletnych okresów. "
        f"Wyniki: {folder}"
    )


    if brakujace:

        print(
            "Okresy bez tabeli "
            "w API: "
            f"{brakujace}. "
            "Szczegóły: "
            "raport_pobierania.csv."
        )


    print(
        "Puste wartości oznaczają "
        "brak danych/rekordu, "
        "a nie automatycznie zero."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        main()


    except KeyboardInterrupt:

        print(
            "\nPrzerwano. "
            "Uruchom ponownie, "
            "aby wznowić pobieranie.",
            file=sys.stderr
        )

        sys.exit(130)


    except (
        RuntimeError,
        OSError,
        ValueError,
        KeyError
    ) as exc:

        print(
            f"\nBŁĄD: {exc}",
            file=sys.stderr
        )

        sys.exit(1)
