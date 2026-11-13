"""Builds Porsche Finder listing-page fixtures.

The records below were captured on 2026-11-13 from the three example listings
(server-rendered HTML of the public detail pages).  Long standard-equipment lists
are trimmed and the Spyder RS seller comment is abbreviated; everything else
(VINs, prices, option codes, image ids, flags) is as published.  The generated
HTML reproduces the page's real framing: a React Server Components stream inside
`self.__next_f.push([1,"..."])`, with `$undefined` sentinels, `$$` escaped dollar
signs and `$<row>` text references.
"""
import json
from pathlib import Path

U = "$undefined"
HERE = Path(__file__).parent


def imgs(ids):
    return [{"id": i, "aspectRatio": 1.5, "shotName": U} for i in ids]


def seller(sid, partner, name, street, city, st, zip_):
    return {"sellerId": sid, "porschePartnerNumber": partner, "name": name,
            "address": f" {street} \n {city}, {st}  {zip_}",
            "addressComponents": {"postalCode": zip_, "city": city, "street": street},
            "formattedCity": f"{city}, {st}, {zip_}", "websiteUrl": U, "commercialRegisterNumber": U}


GT4RS_IMGS = """1be8ca21-6f60-470e-9d40-d16ad45244bc d89d5822-50b6-462c-8c4d-43d3f6c000d7 7f0ce505-510d-48db-b9cb-9e36a660c997
03a63426-e8da-4fd6-9de6-9bbba2746709 df244c19-6d82-4381-b350-3aa6ac1e6cd8 cdb934ce-73f3-4759-8995-a228212dd934
2217db6c-2966-4a01-a54c-7ee3a3a051c3 dc11d9e6-c678-4f86-bade-46486fb25159 834b8472-4bbe-4a8b-85d7-63392f84d6db
350eac52-cd65-47d2-ab82-0f127b855bb7 ca3d1c4e-ae3a-474a-804b-fc1438ef00cf f7ef9d04-5b7d-4b5f-838e-08d241b2aad9
03823694-2585-4da7-8d8c-39f8b26d6d55 98bd8b23-6314-453d-8f98-bf1a43cdb315 3ea20511-8a19-4928-a43d-2b652edbe374
c9c3b833-b627-4387-a041-1fa98ae2b566 5b527d30-a298-40b4-9fe9-0f0314ec5308 bd742dbb-0266-43a7-8276-da827734c515
9089483a-ff41-4d42-8f01-6d8a7e2097dc 970f30bf-466b-457e-a2ad-dfad92508d96 0c54362e-b8a4-47a9-96b0-d274e69baa87
707679b1-d905-42f1-9c6e-147f205fcf4b 3dd5d8da-1604-4c7a-a3d5-4d3a50c4add0 40c7a041-3243-4e69-8eba-69f3965ed7ca
32ee0a80-10b7-46cf-b8bb-a3eb1e7bee53 f14b4b75-ab9d-4a83-b301-47eb1b06c6b9 087529c9-d9e1-42ca-9504-0f307c22ecb0
61b12568-b781-4bc0-9ece-67872ab0fca6 ee7f3f9b-9703-4cd8-b5c5-e7888eb9f6ce 3958b8fc-dd3c-465d-95f0-7588e4c167ff
8ea61f37-f964-4cc6-9652-17d99d62841e a25063ec-072f-4187-b3bb-0596091f18d9""".split()

GT3_IMGS = """d0de0fed-0859-4764-8fcc-5d6403de165d dff1b307-66f7-4f5b-bc2c-fcdbebcd65f2 fc917248-6a52-4245-a045-f9c29afec361
92e8b9fa-fa8c-452b-bc67-0227f180e10a 93a16481-816c-4956-a139-45c1a5bc0777 84a0fe10-64dc-4374-9985-406e988b3c5e
a253ffdf-32db-4d98-a42c-96b68a8fef6e eb9c7c61-0203-4be1-a3e3-ff1332c78e99 3dd9c7d8-67c3-4a2a-b1e2-fa956b8da4d1
cfce3003-ff52-4c66-90e8-bf874ad629b1 ccd3d353-237b-43f9-887e-386907b8d431 e80cc2f7-c491-499c-a5de-d8ee76d276bc
e03429a4-4a62-4e8f-9ae6-06a5ff5ee54c 2a3a1ac8-8fe7-4c2a-b481-aaf6bde9366e 61dfbac7-439c-4305-a355-e06ac3c9605c
3da46b4d-871c-4651-80c9-40990c761dfa 5ae91f76-8d75-42bf-afba-8949a77ed93e 804d772e-55f2-4941-87b5-1d3f6fe864ed
d2dc6e99-71e9-4de1-9566-20dd8d2b8113 f0f38945-5698-4b90-9ff2-77e0cda53e16 8b3ed070-f943-4f9a-8dad-7ba9913e6a92
a5e243d5-44ca-44fe-b137-1f2873b1003f 29cd107b-5e8d-4bf4-9fab-56c4e09e4ff4 00873b7a-9bf4-4d72-b21f-46bb20d69648
ee656ac2-1c30-4a02-a3a8-6ea606d5098e ebd6380e-2c8b-4969-97eb-f9c4d083cdc7 1bea9d9b-837b-4e06-a705-b31d1ac59f10
e355d052-9ff6-45cb-a1f5-2e3c7b97cfeb dd98ca89-0378-4655-a0e5-4762a5157f8a 584ee437-e95f-453d-af89-0533b18db868
73382d4f-949b-4174-adad-750f19d014b1 33122ba3-4126-45d6-a71f-981eeeb373eb a15a784f-1226-443c-804d-5907faf1ccb2
7dc9fbf3-cbc7-40ae-be07-5a8dd02cd483 10da946a-b8c5-4410-b789-53b5927f14bc e8686370-02cd-4ed5-a1b3-a6516ef3fb26""".split()

SPY_EXT = """15b716b9-313d-446c-ae9a-e90404ac4372 266cb74f-44fb-4325-877d-59efc8146454 ca4c0d65-6e85-4adf-8f21-2dd9e11b4cda
5337a120-5d2d-4b31-939c-8f084160a1b0 d0a7773a-e65d-40d0-a431-e85a6179ed36 40421c37-3663-485c-82b4-3bd1b0ee9663
89564186-6fb2-4bed-b153-c09cbda7320e 071162f1-18b0-4db7-9410-552fc386ef42 01949af7-3c79-4a56-a6d5-04eb341f0448
62498f83-5983-4f8f-a382-d1ff8c108ce2 4cec2f7b-1bf6-4f78-a561-9d99b4720bda bf538474-0c23-4941-8da8-4185732aa410
88617f82-80f5-48d9-9a61-a259fc619131 0cf8f269-3eb3-45fd-adaf-45e5ad14f1dc""".split()
SPY_INT = """311689a2-ecd0-4245-964f-449de94cd625 448369b5-6f84-452d-abce-afc74d629eff 9710212b-2d13-43ad-8cb9-836e6b2f2261
94c33bfd-e9f8-483c-91ce-0619c9f90e45 7ed3af57-826e-4445-af80-ae9f60fa7e8e f8bcdb0b-c8b5-4734-952c-e951fe613d19
a52fba43-4ad3-4feb-81d5-d9340f1f1683 cd862b54-d38c-43eb-8407-184879323be4 d944f56e-1440-4ac2-a244-795645d4b500
868e0566-2803-43c0-94b7-f7c86c37bfed ac425e23-0c9d-40cc-b150-67c3dc419804 06622fbd-03fa-4638-ae45-22199e249e86
2ad55b2c-0c13-43a6-b68f-06642ff920a7 fd5ee8bc-e21c-4a96-94a5-e880aa29b62b 928b5421-a8a7-4bf8-9e85-6895281ec7f9
8f38cdcd-b7c5-4b81-9b06-1f8900fa7b2a 5e085e8a-1932-435d-8eae-5222819d199d 6527a706-d81c-49e8-b075-0571022aefaf
9782010d-f332-4c3e-b557-5b29151024ff b8faaf2b-3168-4253-b9f1-67f3de1168eb b4694a9a-3b6a-4287-9a54-548d38fac3b6
7b0e259f-76a8-411d-8a34-b94f16d10eae b0278da3-0a82-45a2-bfe1-b6b9829bb50a b7c65496-57c8-48a9-83c9-06757e6d7341""".split()
SPY_HL = """e61b5ce9-d732-4e79-928e-b77aa34c363f b0594776-01de-4f00-b904-a63e4482ad73 ea40f930-a785-435d-876b-e81aa8cb39c7
e5618587-b0af-4f19-82e4-b60fc03f9a4e adf4f217-a2f9-417a-aa28-20998b13a750 456da220-2980-42d5-94ab-619c2c8cce00
506b32c1-8689-40b0-8378-736c6e66cdc4 27c6f8c4-acc2-44f0-8afe-2f9bb4316189 5d4df511-8070-4b1b-a0aa-8a34b5dcabe9""".split()

GT4RS_COMMENT = ("Porsche Certified! 2025 Porsche 718 Cayman GT4 RS, \nClean Carfax! One owner! 2k miles! Ready to go!,  \n"
                 "Warranty Start: 05-30-2025, \nTransmission: PDK (Automatic), \nEngine: 4.0L H6 DOHC 24V LEV3-ULEV70 493hp, \n"
                 "Exterior Color: Shark Blue, \nInterior Color: Standard Interior in Black/Race-Tex with Arctic Grey Stitching, \n"
                 "Optional Equipment- w/Option Code & Description- \n982850\n718 Cayman GT4 RS\n$164,200, \n41\n"
                 "Standard Interior in Black/Race-Tex with Arctic Grey Stitching, \nD7\nShark Blue\n$3,540, \n474\n"
                 "Front Axle Lift System\n$3,040, \nQR5\nChrono Package\n$300, \n509\nFire Extinguisher\n$140, \n583\n"
                 "Smoking Package, \n581\nLuggage Net in Passenger Footwell, \n"
                 "For more information please contact Porsche Greensboro at 336-252-5588.")
SPY_COMMENT = ("[fixture: abbreviated] Porsche Approved Certified Pre-Owned. 111-point inspection by Porsche-trained "
               "technicians. 2-Year / Unlimited-Mileage Porsche Approved Limited Warranty.")


def opts(groups):
    return [{"name": g, "items": [{"code": c, "name": n, "subtitle": U, "description": "…", "images": [], "video": U, "color": U}
                                  for c, n in items]} for g, items in groups]


def std(groups):
    return [{"name": g, "items": [{"name": n} for n in items]} for g, items in groups]


def listing(lid, inv, title, model, category, year, vin, price_value, price_text, miles, ext, ext_hex, interior,
            trans, sel, approved, total, media_images, important, special, standard, comment, history, breakdown,
            owners, warranty, warranties, stock, commission, body):
    return {
        "listingId": lid, "listed": True, "inventoryId": inv,
        "meta": {"title": category, "imageUrl": f"https://images.finder.porsche.com/{list(media_images.values())[0][0]}/960",
                 "detailsUrl": f"https://finder.porsche.com/us/en-US/details/{title}", "seller": sel, "condition": "preowned",
                 "priceCurrency": "USD", "priceValue": price_value, "numberOfPreviousOwners": owners,
                 "mileage": {"value": miles, "unitCode": "SMI"}, "vin": vin, "model": model, "interiorColor": interior,
                 "color": ext, "transmission": trans, "warranty": warranty, "productionDate": U, "modelYear": year,
                 "firstRegistration": U, "engineType": "PETROL", "interiorName": interior, "modelCategory": category,
                 "bodyType": body, "drivetrain": "REAR_WHEEL_DRIVE", "accidentFree": U},
        "dataLayerListingMeta": "$67:props:value",
        "sections": {
            "gallery": {"imagesType": "real", "totalNumberOfImages": total,
                        "images": imgs(list(media_images.values())[0][:5]), "title": f"{year} Porsche {category}"},
            "mediaCenter": {"title": f"{year} Porsche {category}", "imagesType": "real", "video": U,
                            "images": {k: {"totalCount": len(v), "images": imgs(v)} for k, v in media_images.items()}},
            "title": {"title": f"{year} Porsche {category}", "titleAddition": f"({model})",
                      "subtitle": {"title": "Certified Pre-Owned" if approved else "Pre-Owned", "reserved": U}},
            "summary": {"exteriorColor": {"name": ext, "value": ext_hex}, "interior": {"name": interior}, "rooftopColor": U},
            "info": {"stockNumber": stock, "commissionNumber": commission, "vin": vin, "seller": sel, "price": price_text,
                     "alternativePrice": U, "overviewBreakdown": breakdown, "priceDisclaimer": U,
                     "additionalPriceInformation": "Exclusions apply - see Price Details", "salesIncentives": [],
                     "porscheApproved": approved, "condition": "preowned"},
            "equipment": {"importantEquipmentOptions": [{"value": v, "label": l} for v, l in important],
                          "specialEquipment": opts(special), "standardEquipment": std(standard),
                          "standardEquipmentDisclaimer": U},
            "sellerComment": {"sellerComment": comment},
            "conditionAndHistory": {"porscheApproved": approved, "history": history},
            "importantResources": {"condition": "preowned", "monroneyLabelAvailable": True, "buildsheetAvailable": True,
                                   "modelYear": year},
            "carPassUrl": U,
            "warrantyDetails": {"summary": "Your warranty cover includes:", "warranties": warranties},
        },
        "initialFinancialServiceParametersPromise": "$@73",
    }


APPROVED_W = [{"warrantyType": {"value": "PORSCHE_APPROVED_WARRANTY", "label": "Porsche Approved Warranty"}, "duration": "24 months"},
              {"warrantyType": {"value": "ASSISTANCE", "label": "Porsche Roadside Assistance"}, "duration": "24 months"}]

EXAMPLES = {
    "ZO80OE": (listing(
        "ZO80OE", "6aa836b0b40f713b2f1d987d", "porsche-718-cayman-gt4-rs-preowned-ZO80OE", "982", "718 Cayman GT4 RS", 2025,
        "WP0AE2A80SK271606", 209093, "$$209,093.00", 2088, "Shark Blue", "#0067b9",
        "Standard Interior in Black/Race-Tex with Arctic Grey Stitching", "PDK (Automatic)",
        seller("15000003161", "4501056", "Porsche Greensboro", "5603 Roanne Way", "Greensboro", "NC", "27409"), True, 32,
        {"uncategorized": GT4RS_IMGS},
        [("sport-chrono-package", "Sport Chrono Package"), ("sport-exhaust", "Sports Exhaust System"),
         ("reversing-camera", "ParkAssist (Front and Rear) incl. Reversing Camera"), ("full-bucket-seats", "Full Bucket Seats")],
        [("Engine, transmission and chassis", [("474", "Front Axle Lift System"), ("QR5", "Chrono Package")]),
         ("Interior", [("509", "Fire Extinguisher")]),
         ("Comfort and assistance systems", [("583", "Smoking Package"), ("581", "Luggage Net in Passenger Footwell")])],
        [("Packages", ["RS"]),
         ("Engine, transmission and chassis", ["4.0-Liter Naturally Aspirated Boxer 6", "7-speed Porsche Doppelkupplung (PDK)",
                                               "PASM Sport Suspension (Lowered 30 mm)", "Extended Range Fuel Tank, 16.9 gal"]),
         ("Wheels", ["20\" 718 Cayman GT4 RS Forged Aluminum Wheels"]),
         ("Interior", ["Steering Wheel with 12 O'Clock Marker in Yellow", "Door-Sill Guards in Carbon Fiber"])],
        GT4RS_COMMENT,
        [{"title": "Condition", "value": "Certified Pre-Owned", "description": ["Vehicle with certified quality, complete history, and original parts."]},
         {"title": "Mileage", "description": U, "value": "2,088 mi"}, {"title": "Previous Owners", "value": "1"},
         {"title": "Full Service History", "value": "Yes, every service done"}],
        U, 1, 24, APPROVED_W, "PP6055", "I81410", "Coupé"),
        {"listingId": "ZO80OE", "type": "real_car", "realcarStatus": "preowned", "isPorscheApproved": True,
         "modelRangeName": "718", "interiorColorCode": "41", "exteriorColorCode": "D7", "modelCode": "982850",
         "modelName": "718 Cayman GT4 RS", "modelModelYear": 2025, "priceTotalTotal": 209093}, None),
    "058WLN": (listing(
        "058WLN", "6ac5906686977a2bf0e1aa96", "porsche-911-gt3-preowned-058WLN", "992 I", "911 GT3", 2022,
        "WP0AC2A98NS268519", 240971, "$$240,971.00", 24146, "Shark Blue", "#0067b9",
        "Standard Interior with Race-Tex and Stitching in GT Silver", "Manual",
        seller("6027", "4501444", "Porsche Richmond", "11900 Midlothian Turnpike", "Midlothian", "VA", "23113"), False, 36,
        {"uncategorized": GT3_IMGS},
        [("bose-sound-system", "BOSE® Surround Sound System"), ("sport-chrono-package", "Sport Chrono Package"),
         ("sport-exhaust", "Sports Exhaust System"), ("lifting-system", "Front Axle Lift System"),
         ("pccb", "Porsche Ceramic Composite Brake (PCCB)"), ("lightweight-roof-carbon", "Carbon Fiber Roof"),
         ("reversing-camera", "ParkAssist (Front and Rear) incl. Reversing Camera"), ("front-led", "LED Headlights"),
         ("sports-seats", "Sports Seats")],
        [("Exterior", [("3FF", "Carbon Fiber Roof"), ("2W6", "Exclusive Design Fuel Cap")]),
         ("Engine, transmission and chassis", [("G0W", "6-speed GT Sport Manual Transmission"),
                                               ("8LH", "Chrono Package with Preparation for Lap Trigger"),
                                               ("0I2", "Extended Range Fuel Tank (23.7 gal.)"),
                                               ("1LX", "Porsche Ceramic Composite Brakes (PCCB) with Calipers in High Gloss Black"),
                                               ("2UH", "Front Axle Lift System")]),
         ("Wheels", [("1NX", "Wheels Painted in Satin Black")]),
         ("Interior", [("Q1J", "Adaptive Sport Seats Plus (18-way)"), ("QE1", "Storage Package"),
                       ("1MI", "Steering Wheel and Gear Lever in Black Leather")]),
         ("Audio / Communication", [("9VL", "BOSE® Surround Sound System")]),
         ("Lighting and vision", [("8JT", "LED Headlights in Black incl. Porsche Dynamic Light System (PDLS)")])],
        [("Transmission", ["7-speed Porsche Doppelkupplung (PDK) with short gear ratios and sport programming", "Rear-wheel drive"]),
         ("Brakes", ["Brake calipers in Red", "Electric parking brake"]),
         ("Seats", ["Sport Seats Plus (4-way) with electric backrest angle and seat height adjustment",
                    "Seat bolsters and headrests in black leather", "Seat centers in black Race-Tex"])],
        U,
        [{"title": "Condition", "value": "Pre-Owned", "description": U}, {"title": "Mileage", "description": U, "value": "24,146 mi"},
         {"title": "Full Service History", "value": "Yes, every service done in Porsche Center"}],
        {"items": [{"label": "Vehicle Price", "value": "$$239,992.00", "footnoteIndicator": U},
                   {"label": "Processing Fee", "value": "$$979.00", "footnoteIndicator": U}], "disclaimers": []},
        U, U, [], "U50250", "F00285", "Coupé"),
        {"listingId": "058WLN", "type": "real_car", "realcarStatus": "preowned", "isPorscheApproved": False,
         "modelRangeName": "911", "modelName": "911 GT3", "modelModelYear": 2022, "priceTotalTotal": 240971}, None),
    "5007XV": (listing(
        "5007XV", "6adbee68fa5a1347b6581ed2", "porsche-718-spyder-rs-preowned-5007XV", "982", "718 Spyder RS", 2025,
        "WP0CE2A80SK238226", 213162, "$$213,162.00", 684, "Gt Silver Metallic", "#bcbfc3",
        "Leather/Race-Tex Interior in Black with Arctic Grey Stitching", "PDK (Automatic)",
        seller("1500005356", "4500158", "Porsche San Francisco", "500 8th Street", "San Francisco", "CA", "94103"), True, 47,
        {"exterior": SPY_EXT, "interior": SPY_INT, "highlights": SPY_HL},
        [("bose-sound-system", "BOSE® Surround Sound System"), ("sport-chrono-package", "Sport Chrono Package"),
         ("sport-exhaust", "Sports Exhaust System"), ("reversing-camera", "ParkAssist (Front and Rear) incl. Reversing Camera")],
        [("Exterior", [("539", "Spyder RS Top with Accents in Deviated Color"), ("712", "Deletion of \"Spyder RS\" Logo on Doors")]),
         ("Engine, transmission and chassis", [("474", "Front Axle Lift System"), ("QR5", "Chrono Package"),
                                               ("450", "Porsche Ceramic Composite Brakes (PCCB)")]),
         ("Interior", [("XVX", "Extended Deviated Stitching Interior Package"), ("XBE", "Door Pull Loops in Shark Blue"),
                       ("509", "Fire Extinguisher")]),
         ("Audio / Communication", [("9VL", "BOSE® Surround Sound System"), ("684", "Deletion of SiriusXM® Antenna")]),
         ("Stitching and seat centers", [("00480", "Color Selection for Stitching in Deviated Color - Speed Blue")]),
         ("Comfort and assistance systems", [("583", "Smoking Package"), ("581", "Luggage Net in Passenger Footwell")])],
        [("Packages", ["RS"]), ("Engine, transmission and chassis", ["7-speed Porsche Doppelkupplung (PDK)"])],
        "$72",
        [{"title": "Condition", "value": "Certified Pre-Owned", "description": ["Vehicle with certified quality, complete history, and original parts."]},
         {"title": "Mileage", "description": U, "value": "684 mi"},
         {"title": "Full Service History", "value": "Yes, every service done in Porsche Center"}],
        {"items": [{"label": "Vehicle Price", "value": "$$212,990.00", "footnoteIndicator": U},
                   {"label": "Doc Fee", "value": "$$85.00", "footnoteIndicator": U},
                   {"label": "Electronic Filing Fee", "value": "$$37.00", "footnoteIndicator": U},
                   {"label": "Emissions Testing Fee", "value": "$$50.00", "footnoteIndicator": U}], "disclaimers": []},
        U, 24, APPROVED_W, "CSK238226", "J24747", "Cabriolet"),
        {"listingId": "5007XV", "type": "real_car", "realcarStatus": "preowned", "isPorscheApproved": True,
         "modelRangeName": "718", "interiorColorCode": "51", "exteriorColorCode": "U2", "modelCode": "982890",
         "modelName": "718 Spyder RS", "modelModelYear": 2025, "priceTotalTotal": 213162}, SPY_COMMENT),
}


def page(main, datalayer, text_row):
    rows = ['0:{"P":null,"c":["","us","en-US","details","x"]}',
            '67:["$","$L6a",null,{"value":{"car":' + json.dumps(datalayer, separators=(",", ":")) + "}}]",
            '3d:["$","$L66",null,{"languageTag":"en-US","messages":{"a.b":"Open in Car Configurator"},"listing":'
            + json.dumps(main, separators=(",", ":"), ensure_ascii=False) + "}]"]
    if text_row:
        rows.append(f"72:T{len(text_row.encode('utf-8')):x},{text_row}")
    rsc = "\n".join(rows) + "\n"
    ld = {"@context": "https://schema.org", "@graph": [{"@type": ["Product", "Car"], "name": main["meta"]["title"],
                                                         "vehicleIdentificationNumber": main["meta"]["vin"]}]}
    first = list(main["sections"]["mediaCenter"]["images"].values())[0]["images"][0]["id"]
    return ("<!DOCTYPE html><html><head><title>Used Porsche " + main["meta"]["title"] + " for sale at "
            + main["meta"]["seller"]["name"] + '</title><meta property="og:image" content="https://images.finder.porsche.com/'
            + first + '/960"><script type="application/ld+json">' + json.dumps(ld) + "</script></head><body>"
            '<picture><source type="image/avif" srcSet="https://images.finder.porsche.com/' + first + "/320.avif 320w, "
            "https://images.finder.porsche.com/" + first + '/640.avif 640w"></picture>'
            "<script>self.__next_f.push([1," + json.dumps(rsc, ensure_ascii=False) + "])</script></body></html>")


if __name__ == "__main__":
    for lid, (main, dl, text) in EXAMPLES.items():
        (HERE / f"finder_{lid}.html").write_text(page(main, dl, text), encoding="utf-8")
    print("wrote", ", ".join(f"finder_{k}.html" for k in EXAMPLES))
