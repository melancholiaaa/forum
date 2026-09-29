#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Eksport gazu z Polski według kraju partnera/przeznaczenia.
Źródło: API DBW GUS.

Pobierane towary:
    CN 27111100 - gaz ziemny skroplony
    CN 27112100 - gaz ziemny w stanie gazowym

Wyniki:
    gus_gaz_export/
        gus_gaz_export_miesieczne.csv
        gus_gaz_export_roczne.csv
        raport_pobierania_export.csv
        metadane_gus_export.json
        cache/
"""

import argparse
import calendar
import csv
import gzip
import json
import re
import sys
import time

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# ============================================================
# USTAWIENIA
# ============================================================

API = "https://api-dbw.stat.gov.pl/api/1.2.0/"

KODY_CN = {
    "27111100",
    "27112100",
}

ROK_OD = 2018
ROK_DO = None

ROZMIAR_STRONY = 5000


# ============================================================
# KLUCZ API GUS
# ============================================================
#
# WKLEJ TUTAJ DOKŁADNIE TEN SAM KLUCZ,
# KTÓRY MASZ W DZIAŁAJĄCYM KODZIE IMPORTOWYM.
#
# ZOSTAW CUDZYSŁOWY.
# JEŻELI KLUCZ KOŃCZY SIĘ "=" TO "=" TEŻ MUSI ZOSTAĆ.
#
# PRZYKŁAD:
# KLUCZ_API = "abc123xyz="
#
# ============================================================

KLUCZ_API = "WKLEJ_TUTAJ_SWOJ_KLUCZ_API"


# ============================================================
# KATEGORIE SPECJALNE GUS
# ============================================================

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
]


# ============================================================
# POMOCNICZE
# ============================================================

def zapisz_json(sciezka, dane):

    sciezka.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    tmp = sciezka.with_suffix(
        sciezka.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            dane,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    tmp.replace(sciezka)


def zapisz_csv(
    sciezka,
    kolumny,
    wiersze
):

    sciezka.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    tmp = sciezka.with_suffix(
        ".csv.tmp"
    )

    with tmp.open(
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

        writer.writerows(
            wiersze
        )

    tmp.replace(sciezka)


# ============================================================
# KLIENT API
# ============================================================

class KlientGUS:

    def __init__(self, klucz):

        klucz = (
            klucz
            .strip()
            .strip('"')
            .strip("'")
        )

        if not klucz:

            raise RuntimeError(
                "KLUCZ_API jest pusty."
            )

        if (
            klucz
            == "WKLEJ_TUTAJ_SWOJ_KLUCZ_API"
        ):

            raise RuntimeError(
                "Nie wkleiłeś klucza API. "
                "Wpisz swój klucz w zmiennej KLUCZ_API."
            )

        self.klucz = klucz

        self.ostatnie_zadanie = 0.0


    def pobierz(
        self,
        endpoint,
        parametry=None,
        dopuszczaj_404=False
    ):

        parametry = {
            "lang": "pl",
            **(parametry or {})
        }

        url = (
            API
            + endpoint
            + "?"
            + urlencode(parametry)
        )


        for proba in range(6):

            # Bezpiecznie poniżej limitu API.
            czekaj = max(
                0,
                2.05
                - (
                    time.monotonic()
                    - self.ostatnie_zadanie
                )
            )

            if czekaj:

                time.sleep(czekaj)


            self.ostatnie_zadanie = (
                time.monotonic()
            )


            naglowki = {

                "Accept":
                    "application/json",

                "Accept-Encoding":
                    "gzip",

                # TU KLUCZ TRAFIA DO API GUS
                "X-ClientId":
                    self.klucz,

                "User-Agent":
                    "GUS-DBW-gaz-export/2.0",
            }


            try:

                req = Request(
                    url,
                    headers=naglowki
                )

                with urlopen(
                    req,
                    timeout=90
                ) as response:

                    tresc = (
                        response.read()
                    )

                    if (
                        response.headers.get(
                            "Content-Encoding"
                        )
                        == "gzip"
                    ):

                        tresc = (
                            gzip.decompress(
                                tresc
                            )
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


                if e.code == 401:

                    raise RuntimeError(
                        "HTTP 401: GUS nie zaakceptował klucza API. "
                        "Sprawdź, czy KLUCZ_API został skopiowany "
                        "w całości, razem z końcowym znakiem '='."
                    ) from None


                if e.code == 403:

                    raise RuntimeError(
                        "HTTP 403: GUS odrzucił dostęp. "
                        "Klucz może być nieaktywny albo nieprawidłowy."
                    ) from None


                if e.code in (
                    408,
                    429,
                    500,
                    502,
                    503,
                    504
                ):

                    powod = (
                        f"HTTP {e.code}"
                    )

                else:

                    raise RuntimeError(
                        f"HTTP {e.code} dla "
                        f"{endpoint}"
                    ) from None


            except (
                URLError,
                TimeoutError,
                OSError,
                ValueError
            ) as e:

                powod = (
                    type(e).__name__
                )


            if proba == 5:

                raise RuntimeError(
                    f"Nie udało się pobrać "
                    f"{endpoint}: {powod}"
                )


            opoznienie = min(
                60,
                5 * (2 ** proba)
            )


            if powod == "HTTP 429":

                opoznienie = 120


            print(
                f"{powod}; "
                f"ponawiam za "
                f"{opoznienie} s.",
                flush=True
            )


            time.sleep(
                opoznienie
            )


    def slownik(
        self,
        nazwa,
        klucz
    ):

        wynik = {}

        strona = 1


        while True:

            dane = self.pobierz(

                "dictionaries/"
                + nazwa,

                {
                    "page":
                        strona,

                    "page-size":
                        ROZMIAR_STRONY,
                }
            )


            if (
                not isinstance(
                    dane,
                    dict
                )
                or
                not isinstance(
                    dane.get(
                        "data"
                    ),
                    list
                )
            ):

                raise RuntimeError(
                    f"Nieprawidłowa odpowiedź "
                    f"słownika {nazwa}."
                )


            for x in dane["data"]:

                wynik[
                    x[klucz]
                ] = x


            if (
                strona
                >= dane["page-count"]
            ):

                break


            strona += 1


        return wynik


# ============================================================
# AUTOMATYCZNE WYSZUKIWANIE ZMIENNEJ "EKSPORT TOWARÓW"
# ============================================================

def znajdz_zmienna_export(
    klient
):

    print(
        "Szukam w GUS zmiennej "
        "'Eksport towarów'...",
        flush=True
    )


    strona = 0

    znalezione = []


    while True:

        dane = klient.pobierz(

            "variable/"
            "variable-section-periods",

            {
                "ile-na-stronie":
                    ROZMIAR_STRONY,

                "numer-strony":
                    strona,
            }
        )


        if (
            not isinstance(
                dane,
                dict
            )
            or
            not isinstance(
                dane.get(
                    "data"
                ),
                list
            )
        ):

            raise RuntimeError(
                "Nie udało się odczytać "
                "listy zmiennych GUS."
            )


        for rekord in dane["data"]:

            teksty = [

                str(v)

                for v
                in rekord.values()

                if isinstance(
                    v,
                    str
                )
            ]


            calosc = (
                " | ".join(
                    teksty
                )
                .casefold()
            )


            if (
                "eksport towarów"
                in calosc
                or
                "eksport towarow"
                in calosc
            ):

                znalezione.append(
                    rekord
                )


        page_count = (
            dane.get(
                "page-count"
            )
        )


        if page_count is None:

            break


        # API GUS stosuje numerowanie
        # stron od 0 dla danych.
        if strona >= page_count:

            break


        strona += 1


    if not znalezione:

        raise RuntimeError(
            "Nie znaleziono w API GUS "
            "zmiennej 'Eksport towarów'."
        )


    # Próbujemy znaleźć pole id-zmienna.
    id_zmiennej = None


    for rekord in znalezione:

        for klucz in (
            "id-zmienna",
            "id-zmiennej",
            "id_zmienna"
        ):

            if klucz in rekord:

                id_zmiennej = (
                    rekord[klucz]
                )

                break


        if id_zmiennej is not None:

            break


    if id_zmiennej is None:

        raise RuntimeError(
            "Znaleziono eksport, ale API "
            "nie zwróciło pola id-zmienna."
        )


    print(
        f"Znaleziono "
        f"ID zmiennej eksportu: "
        f"{id_zmiennej}",
        flush=True
    )


    return (
        int(
            id_zmiennej
        ),
        znalezione
    )


# ============================================================
# WYBÓR PRZEKROJU
# ============================================================

def znajdz_przekroj_gazowy(
    klient,
    id_zmiennej,
    rekordy_export
):

    kandydaci = set()


    for rekord in rekordy_export:

        rekord_id = None


        for klucz in (
            "id-przekroj",
            "id-przekroju",
            "id_przekroj"
        ):

            if klucz in rekord:

                rekord_id = (
                    rekord[klucz]
                )

                break


        rekord_zmienna = (
            rekord.get(
                "id-zmienna",
                rekord.get(
                    "id-zmiennej"
                )
            )
        )


        if (
            rekord_id is not None
            and
            rekord_zmienna is not None
            and
            int(rekord_zmienna)
            == int(id_zmiennej)
        ):

            kandydaci.add(
                int(
                    rekord_id
                )
            )


    if not kandydaci:

        # Awaryjnie próbujemy przekroju,
        # który działał w danych importowych.
        kandydaci.add(1434)


    for id_przekroju in sorted(
        kandydaci
    ):

        try:

            pozycje = klient.pobierz(

                "variable/"
                "variable-section-position",

                {
                    "id-przekroj":
                        id_przekroju
                }
            )

        except RuntimeError:

            continue


        if not isinstance(
            pozycje,
            list
        ):

            continue


        nazwy_wymiarow = {

            x.get(
                "nazwa-wymiar",
                ""
            )

            for x
            in pozycje
        }


        ma_kraje = (
            "Kraje towary"
            in nazwy_wymiarow
        )


        ma_cn = any(

            "CN"
            in nazwa

            for nazwa
            in nazwy_wymiarow
        )


        symbole = {

            str(
                x.get(
                    "symbol",
                    ""
                )
            )

            for x
            in pozycje
        }


        ma_gaz = (
            KODY_CN
            .issubset(
                symbole
            )
        )


        if (
            ma_kraje
            and
            ma_cn
            and
            ma_gaz
        ):

            print(
                f"Znaleziono właściwy "
                f"przekrój: "
                f"{id_przekroju}",
                flush=True
            )

            return (
                id_przekroju,
                pozycje
            )


    raise RuntimeError(
        "Nie znaleziono przekroju eksportowego "
        "z krajami i kodami CN "
        "27111100 / 27112100."
    )


# ============================================================
# METADANE
# ============================================================

def wczytaj_metadane(
    klient
):

    ID_ZMIENNEJ, rekordy = (
        znajdz_zmienna_export(
            klient
        )
    )


    meta = klient.pobierz(

        "variable/"
        "variable-meta",

        {
            "id-zmiennej":
                ID_ZMIENNEJ
        }
    )


    print(
        "Zmienna:",
        meta.get(
            "nazwa"
        )
    )


    ID_PRZEKROJU, pozycje = (
        znajdz_przekroj_gazowy(
            klient,
            ID_ZMIENNEJ,
            rekordy
        )
    )


    # ========================================================
    # KRAJE
    # ========================================================

    kraje = {

        x["id-pozycja"]:
            x

        for x
        in pozycje

        if (
            x.get(
                "nazwa-wymiar"
            )
            == "Kraje towary"

            and

            x.get(
                "symbol"
            )
            not in (
                "00",
                "EU"
            )

            and

            x.get(
                "nazwa-pozycja",
                ""
            )
            .strip()
            .casefold()
            != "ogółem"
        )
    }


    # ========================================================
    # CN
    # ========================================================

    towary = {

        x["id-pozycja"]:
            dict(x)

        for x
        in pozycje

        if (
            "CN"
            in x.get(
                "nazwa-wymiar",
                ""
            )

            and

            str(
                x.get(
                    "symbol",
                    ""
                )
            )
            in KODY_CN
        )
    }


    znalezione_cn = {

        str(
            x.get(
                "symbol"
            )
        )

        for x
        in towary.values()
    }


    if (
        znalezione_cn
        != KODY_CN
    ):

        raise RuntimeError(
            "Nie znaleziono obu "
            "kodów gazu CN."
        )


    if not kraje:

        raise RuntimeError(
            "Nie znaleziono "
            "krajów w przekroju."
        )


    # ========================================================
    # JEDNOSTKI
    # ========================================================

    for x in towary.values():

        nazwa = (
            x.get(
                "nazwa-pozycja",
                ""
            )
        )


        m = re.search(
            r"\[([^\[\]]+)\]\s*$",
            nazwa
        )


        if m:

            x[
                "jednostka"
            ] = (
                m.group(1)
            )

        else:

            x[
                "jednostka"
            ] = ""


    # ========================================================
    # OKRESY
    # ========================================================

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
            x.get(
                "id-czestotliwosc"
            )
            == 3

            and

            x.get(
                "id-typ"
            )
            == 1

            and m
        ):

            miesiace[
                int(
                    m.group(1)
                )
            ] = ident


    roczne = [

        ident

        for ident, x
        in okresy.items()

        if (
            x.get(
                "opis"
            )
            ==
            "rok - dane roczne - rok"
        )
    ]


    if len(
        miesiace
    ) != 12:

        raise RuntimeError(
            "Nie znaleziono "
            "12 miesięcy w słowniku GUS."
        )


    if len(
        roczne
    ) != 1:

        raise RuntimeError(
            "Nie rozpoznano "
            "okresu rocznego."
        )


    serie = {

        x[
            "id-czestotliwosc"
        ]:
            x

        for x
        in meta.get(
            "przekroje",
            []
        )

        if (
            x.get(
                "id-przekroj"
            )
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
            "Wybrany przekrój "
            "nie ma jednocześnie "
            "danych miesięcznych "
            "i rocznych."
        )


    return {

        "id_zmiennej":
            ID_ZMIENNEJ,

        "id_przekroju":
            ID_PRZEKROJU,

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
            klient.slownik(
                "flag-dictionary",
                "id-flaga"
            ),

        "braki":
            klient.slownik(
                "no-value-dictionary",
                "id-brak-wartosci"
            ),

        "tajnosc":
            klient.slownik(
                "confidentionality-dictionary",
                "id-tajnosci"
            ),
    }


# ============================================================
# ODCZYT WYMIARU
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

            return (
                wiersz.get(
                    f"id-pozycja-{i}"
                )
            )


    raise RuntimeError(
        f"Nie znaleziono "
        f"wymiaru {wymiar}."
    )


# ============================================================
# POBIERANIE OKRESU
# ============================================================

def pobierz_okres(
    klient,
    meta,
    rok,
    miesiac,
    cache,
    odswiez=False
):

    id_zmiennej = (
        meta[
            "id_zmiennej"
        ]
    )

    id_przekroju = (
        meta[
            "id_przekroju"
        ]
    )


    okres = (

        meta[
            "miesiace"
        ][
            miesiac
        ]

        if miesiac

        else

        meta[
            "okres_roczny"
        ]
    )


    etykieta = (

        f"{rok} M{miesiac}"

        if miesiac

        else str(
            rok
        )
    )


    aktualizacja = (

        meta[
            "serie"
        ][
            3
            if miesiac
            else 1
        ]
        .get(
            "aktualizacja-ostatnia",
            ""
        )
    )


    plik = (

        cache

        / (
            f"{rok}_"
            f"{okres}.json"
        )
    )


    sygnatura = [

        API,
        id_zmiennej,
        id_przekroju,
        sorted(
            KODY_CN
        ),
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

        "dane":
            [],

        "gotowe":
            False,
    }


    if (
        plik.exists()
        and
        not odswiez
    ):

        zapisany = json.loads(
            plik.read_text(
                encoding="utf-8"
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
            f"cache",
            flush=True
        )

        return stan


    wymiar_cn = (

        next(
            iter(
                meta[
                    "towary"
                ].values()
            )
        )[
            "id-wymiar"
        ]
    )


    wymiar_kraju = (

        next(
            iter(
                meta[
                    "kraje"
                ].values()
            )
        )[
            "id-wymiar"
        ]
    )


    while not stan[
        "gotowe"
    ]:

        strona = (
            stan[
                "nastepna_strona"
            ]
        )


        d = klient.pobierz(

            "variable/"
            "variable-data-section",

            {
                "id-zmienna":
                    id_zmiennej,

                "id-przekroj":
                    id_przekroju,

                "id-rok":
                    rok,

                "id-okres":
                    okres,

                "ile-na-stronie":
                    ROZMIAR_STRONY,

                "numer-strony":
                    strona,
            },

            dopuszczaj_404=(
                strona == 0
            )
        )


        if d is None:

            print(
                f"{etykieta}: "
                f"brak danych",
                flush=True
            )

            return None


        dane = (
            d.get(
                "data",
                []
            )
        )


        if (
            not dane
            and
            strona == 0
        ):

            return None


        ostatnia = (
            d.get(
                "page-count",
                0
            )
        )


        for w in dane:

            cn = (
                pozycja_wymiaru(
                    w,
                    wymiar_cn
                )
            )


            if (
                cn
                not in
                meta[
                    "towary"
                ]
            ):

                continue


            kraj = (
                pozycja_wymiaru(
                    w,
                    wymiar_kraju
                )
            )


            if (
                kraj
                not in
                meta[
                    "kraje"
                ]
            ):

                continue


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


        stan[
            "nastepna_strona"
        ] = (
            strona + 1
        )


        stan[
            "liczba_stron"
        ] = (
            strona + 1
        )


        stan[
            "gotowe"
        ] = (
            strona
            >= ostatnia
        )


        stan[
            "data_pobrania"
        ] = (
            datetime.now(
                timezone.utc
            )
            .isoformat(
                timespec="seconds"
            )
        )


        zapisz_json(
            plik,
            stan
        )


        print(

            f"{etykieta}: "
            f"strona "
            f"{strona + 1}/"
            f"{ostatnia + 1}, "
            f"gaz: "
            f"{len(stan['dane'])}",

            flush=True
        )


    return stan


# ============================================================
# AKTYWNOŚĆ KRAJU / CN
# ============================================================

def aktywna(
    pozycja,
    rok,
    miesiac
):

    poczatek = (
        date(
            rok,
            miesiac or 1,
            1
        )
        .isoformat()
    )


    koniec = (
        date(
            rok,
            miesiac or 12,

            calendar.monthrange(
                rok,
                miesiac or 12
            )[1]
        )
        .isoformat()
    )


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
# OPISY SŁOWNIKÓW
# ============================================================

def opis(
    slownik,
    ident
):

    if ident is None:

        return ""


    return (
        slownik
        .get(
            ident,
            {}
        )
        .get(
            "nazwa",
            ""
        )
    )


# ============================================================
# CSV
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

        key=lambda x:
            x[1]
            .get(
                "nazwa-pozycja",
                ""
            )
    )


    towary = sorted(

        meta[
            "towary"
        ].items(),

        key=lambda x:
            x[1]
            .get(
                "symbol",
                ""
            )
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


        rekordy = {

            (
                w[
                    "kraj_id"
                ],
                w[
                    "cn_id"
                ]
            ):
                w

            for w
            in stan[
                "dane"
            ]
        }


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


                w = (
                    rekordy.get(
                        (
                            kraj_id,
                            cn_id
                        ),
                        {}
                    )
                )


                wartosc = (
                    w.get(
                        "wartosc"
                    )
                )


                if wartosc is not None:

                    status = (
                        "wartosc_opublikowana"
                    )


                elif not w:

                    status = (
                        "brak_rekordu_w_api"
                    )


                elif (
                    meta[
                        "tajnosc"
                    ]
                    .get(
                        w.get(
                            "id-tajnosci"
                        ),
                        {}
                    )
                    .get(
                        "oznaczenie"
                    )
                    == "(:)"
                ):

                    status = (
                        "tajemnica_statystyczna"
                    )

                    wartosc = None


                elif (
                    w.get(
                        "id-brak-wartosci"
                    )
                    == 42
                ):

                    status = (
                        "zjawisko_nie_wystapilo"
                    )


                else:

                    status = (
                        "brak_wartosci_w_api"
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
                        kraj.get(
                            "nazwa-pozycja",
                            ""
                        ),

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
                                in
                                KATEGORIE_SPECJALNE
                            )

                            else

                            "kraj_lub_terytorium"
                        ),

                    "kod_cn":
                        cn.get(
                            "symbol",
                            ""
                        ),

                    "towar":
                        cn.get(
                            "nazwa-pozycja",
                            ""
                        ),

                    "wartosc":
                        (
                            ""

                            if wartosc
                            is None

                            else

                            format(
                                Decimal(
                                    str(
                                        wartosc
                                    )
                                ),
                                "f"
                            )
                            .replace(
                                ".",
                                ","
                            )
                        ),

                    "jednostka":
                        cn.get(
                            "jednostka",
                            ""
                        ),

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
                }


# ============================================================
# ZAPIS WYNIKÓW
# ============================================================

def eksportuj(
    folder,
    meta,
    stany,
    raport
):

    zapisz_csv(

        folder
        / "gus_gaz_export_miesieczne.csv",

        KOLUMNY,

        wiersze_csv(
            meta,
            stany,
            False
        )
    )


    zapisz_csv(

        folder
        / "gus_gaz_export_roczne.csv",

        KOLUMNY,

        wiersze_csv(
            meta,
            stany,
            True
        )
    )


    zapisz_csv(

        folder
        / "raport_pobierania_export.csv",

        [
            "okres",
            "typ_okresu",
            "status",
            "strony",
            "rekordy_gazu",
            "uwagi",
        ],

        raport
    )


# ============================================================
# MAIN
# ============================================================

def main():

    # ========================================================
    # KLUCZ API
    # ========================================================

    klient = KlientGUS(
        KLUCZ_API
    )


    parser = argparse.ArgumentParser(
        description=__doc__
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
            or
            date.today().year
        ),
        dest="rok_do"
    )


    if "__file__" in globals():

        katalog_bazowy = (

            Path(
                __file__
            )
            .resolve()
            .parent
        )

    else:

        # Jupyter
        katalog_bazowy = (
            Path.cwd()
        )


    parser.add_argument(

        "--katalog",

        type=Path,

        default=(
            katalog_bazowy
            / "gus_gaz_export"
        )
    )


    parser.add_argument(

        "--odswiez",

        action="store_true"
    )


    # Jupyter przekazuje:
    # -f kernel-xxxxx.json
    #
    # dlatego używamy parse_known_args.

    if (
        "ipykernel"
        in sys.modules
    ):

        args, _ = (
            parser.parse_known_args()
        )

    else:

        args = (
            parser.parse_args()
        )


    if not (
        2004
        <= args.rok_od
        <= args.rok_do
        <= date.today().year
    ):

        raise RuntimeError(
            "Nieprawidłowy zakres lat."
        )


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


    print(
        "Sprawdzam połączenie z API GUS...",
        flush=True
    )


    # To od razu pokaże,
    # czy KLUCZ_API działa.

    wersja = klient.pobierz(
        "version"
    )


    print(
        "Połączenie z GUS OK."
    )


    print(
        "Wersja API:",
        wersja
    )


    # ========================================================
    # METADANE
    # ========================================================

    meta = (
        wczytaj_metadane(
            klient
        )
    )


    zapisz_json(

        folder
        / "metadane_gus_export.json",

        meta
    )


    print(
        "\nID zmiennej:",
        meta[
            "id_zmiennej"
        ]
    )


    print(
        "ID przekroju:",
        meta[
            "id_przekroju"
        ]
    )


    print(
        "\nPobierane towary:"
    )


    for x in sorted(

        meta[
            "towary"
        ].values(),

        key=lambda x:
            x.get(
                "symbol",
                ""
            )
    ):

        print(
            " ",
            x.get(
                "symbol"
            ),
            "-",
            x.get(
                "nazwa-pozycja"
            )
        )


    # ========================================================
    # ZADANIA
    # ========================================================

    dzis = (
        date.today()
    )


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


            if (
                rok
                == dzis.year

                and

                (
                    miesiac == 0

                    or

                    miesiac
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
                    str(
                        seria.get(
                            "szereg-czasowy",
                            ""
                        )
                    )
                )
            ]


            if (
                lata
                and
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


    print(
        "\nLiczba okresów "
        "do sprawdzenia:",
        len(
            zadania
        )
    )


    stany = []

    raport = []


    # ========================================================
    # POBIERANIE
    # ========================================================

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
                        f"{rok} M{miesiac}"

                        if miesiac

                        else

                        str(
                            rok
                        )
                    ),

                "typ_okresu":
                    (
                        "miesieczny"

                        if miesiac

                        else

                        "roczny"
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

                stan = (
                    pobierz_okres(

                        klient,
                        meta,
                        rok,
                        miesiac,
                        cache,
                        args.odswiez
                    )
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
                    "Okres nie został "
                    "pobrany kompletnie."
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
                    "Brak opublikowanych "
                    "danych."
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


            # zapis po każdym roku

            if (

                indeks
                == len(
                    zadania
                )
                - 1

                or

                (
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

        if raport:

            eksportuj(
                folder,
                meta,
                stany,
                raport
            )


    if not stany:

        raise RuntimeError(
            "Nie pobrano żadnego "
            "kompletnego okresu."
        )


    print(
        "\n============================"
    )

    print(
        "GOTOWE"
    )

    print(
        "============================"
    )


    print(
        "Folder:",
        folder
    )


    print(
        "\nPliki:"
    )

    print(
        "gus_gaz_export_miesieczne.csv"
    )

    print(
        "gus_gaz_export_roczne.csv"
    )

    print(
        "raport_pobierania_export.csv"
    )

    print(
        "metadane_gus_export.json"
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
            "aby kontynuować.",
            file=sys.stderr
        )


    except Exception as exc:

        print(
            "\nBŁĄD:",
            exc,
            file=sys.stderr
        )

        raise
