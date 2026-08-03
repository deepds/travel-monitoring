# Доступные данные через API

## 1. RZD API (rzd-api)

**Источник:** неофициальный API [`ticket.rzd.ru`](https://ticket.rzd.ru)
**Способ получения:** Python-пакет `rzd-api` (PyPI), внутреннее обращение к REST API РЖД через HTTP-клиент

### Методы и доступные поля данных

#### `search_tickets` — поиск поездов по маршруту

| Поле | Тип | Описание |
|------|------|-----------|
| `number` | `str` | номер поезда (напр. `047Й`) |
| `display_number` | `str \| None` | отображаемый номер поезда |
| `origin_name` | `str \| None` | название станции отправления (напр. «Москва») |
| `origin_code` | `str \| None` | код станции отправления (напр. `2000001`) |
| `origin_station_name` | `str` (raw) | полное название вокзала/станции отправления |
| `destination_name` | `str \| None` | название станции прибытия (напр. «Саратов») |
| `destination_code` | `str \| None` | код станции прибытия |
| `destination_station_name` | `str` (raw) | полное название вокзала/станции прибытия |
| `departure_time` | `str \| None` | дата и время отправления (ISO 8601, напр. `2026-08-13T14:05:00`) |
| `arrival_time` | `str \| None` | дата и время прибытия (ISO 8601) |
| `local_departure_time` | `str` (raw) | локальное время отправления |
| `local_arrival_time` | `str` (raw) | локальное время прибытия |
| `min_price` | `float \| None` | минимальная цена билета (₽) |
| `available_places` | `int \| None` | количество свободных мест |
| `trip_duration` | `str` (raw) | длительность поездки |
| `trip_distance` | `int` (raw) | расстояние поездки (км) |
| `route_number` | `str \| None` | номер маршрута |
| `provider` | `str \| None` | провайдер услуги |
| `carriers` | `list[str]` (raw) | список перевозчиков (напр. `ФПК`) |
| `carrier_display_names` | `list[str]` (raw) | отображаемые названия перевозчиков |
| `has_electronic_registration` | `bool` (raw) | доступна ли электронная регистрация |
| `has_two_storey_cars` | `bool` (raw) | есть ли двухэтажные вагоны |
| `has_dynamic_pricing_cars` | `bool` (raw) | есть ли вагоны с динамическим ценообразованием |
| `has_special_sale_mode` | `bool` (raw) | специальный режим продажи |
| `is_branded` | `bool` (raw) | фирменный ли поезд |
| `is_sale_forbidden` | `bool` (raw) | приостановлены ли продажи |
| `is_ticket_print_required` | `bool` (raw) | требуется ли печать билета для посадки |
| `is_wait_list_available` | `bool` (raw) | доступен ли лист ожидания |
| `is_suburban` | `bool` (raw) | пригородный ли поезд |
| `transport_type` | `str` (raw) | тип транспорта |
| `train_name` | `str` (raw) | название поезда |
| `train_description` | `str` (raw) | описание поезда |
| `train_brand_code` | `str` (raw) | код бренда поезда |
| `train_class_names` | `list[str]` (raw) | названия классов поезда |
| `category_id` | `str` (raw) | ID категории |
| `booking_system` | `str` (raw) | система бронирования |
| `boarding_system_types` | `list` (raw) | типы систем посадки |
| `car_services` | `list` (raw) | услуги в поезде |
| `schedule_id` | `str` (raw) | ID расписания |
| `departure_stop_time` | `str` (raw) | время стоянки при отправлении |
| `arrival_stop_time` | `str` (raw) | время стоянки при прибытии |
| `car_transportations_free_places_count` | `int` (raw) | свободные места для автоперевозки |
| `baggage_total_place_quantity` | `int` (raw) | количество багажных мест |

#### `search_tickets` → `CarGroup` (группировка вагонов)

| Поле | Тип | Описание |
|------|------|-----------|
| `car_type` | `str \| None` | тип вагона (`Compartment`/`ReservedSeat`/`Luxury`/`SV`/`Sitting`) |
| `car_type_name` | `str` (raw) | русское название типа (КУПЕ / ПЛАЦ / СВ / ЛЮКС / СИДЯЧИЙ) |
| `min_price` | `float \| None` | минимальная цена в группе (₽) |
| `max_price` | `float \| None` | максимальная цена в группе (₽) |
| `available_places` | `int \| None` | общее количество свободных мест |
| `place_quantity` | `int` (raw) | общее количество мест в группе |
| `lower_place_quantity` | `int` (raw) | количество свободных нижних мест |
| `upper_place_quantity` | `int` (raw) | количество свободных верхних мест |
| `lower_side_place_quantity` | `int` (raw) | количество свободных нижних боковых мест |
| `upper_side_place_quantity` | `int` (raw) | количество свободных верхних боковых мест |
| `male_place_quantity` | `int` (raw) | количество мужских мест |
| `female_place_quantity` | `int` (raw) | количество женских мест |
| `large_family_place_quantity` | `int` (raw) | количество мест для многодетных семей |
| `empty_cabin_quantity` | `int` (raw) | количество пустых купе |
| `mixed_cabin_quantity` | `int` (raw) | количество смешанных купе |
| `places_with_conditional_refundable_quantity` | `int` (raw) | места с условно-возвратным тарифом |
| `has_places_with_child` | `bool` (raw) | есть ли места для проезда с детьми |
| `has_places_for_large_family` | `bool` (raw) | есть ли места для многодетных семей |
| `service_classes` | `list[str]` (raw) | классы обслуживания (2Ф, 2Э, 2К, 2Л, 3Э, 1Э и т.д.) |
| `service_costs` | `list[float]` (raw) | стоимости сервисных сборов (₽) |
| `car_descriptions` | `list[str]` (raw) | описания вагонов (напр. `Ж У0`) |
| `availability_indication` | `str` (raw) | индикатор доступности (`Available` и др.) |
| `is_sale_forbidden` | `bool` (raw) | приостановлены ли продажи для данной группы |
| `carriers` | `list[str]` (raw) | перевозчики в группе |

#### `get_carriages` — детальная информация о вагонах

| Поле | Тип | Описание |
|------|------|-----------|
| `number` | `str \| None` | номер вагона (напр. `05`) |
| `car_type` | `str \| None` | тип вагона (`Compartment`/`ReservedSeat`/`Luxury`/`SV`/`Sitting`) |
| `car_type_name` | `str \| None` | русское название типа (КУПЕ / ПЛАЦ / СВ / ЛЮКС / СИДЯЧИЙ) |
| `car_sub_type` | `str \| None` | подтип вагона (напр. `48К`, `36П`) |
| `scheme_id` | `int \| None` | ID схемы вагона |
| `scheme_name` | `str \| None` | название схемы (напр. `48К`) |
| `service_class` | `str \| None` | класс обслуживания (2Ф, 2Э, 2К, 2Л, 3Э, 1Э и т.д.) |
| `service_class_name` | `str \| None` | название класса обслуживания |
| `service_class_transcript` | `str` (raw) | полная расшифровка класса (напр. «Вагон повышенной комфортности. 4-местные купе...») |
| `car_description` | `str` (raw) | описание вагона (напр. `Ж У0`) |
| `min_price` | `float \| None` | минимальная цена в вагоне (₽) |
| `max_price` | `float \| None` | максимальная цена в вагоне (₽) |
| `service_cost` | `float \| None` | стоимость сервисного сбора (₽) |
| `free_places` | `str \| None` | номера свободных мест через запятую (напр. `18, 26, 28, 30`) |
| `free_places_by_compartments` | `list[dict]` (raw) | свободные места с разбивкой по купе (`CompartmentNumber`, `Places`) |
| `available_places` | `int \| None` | количество свободных мест |
| `place_quantity` | `int` (raw) | общее количество мест |
| `services` | `list[str]` | список услуг (Bedclothes, BioToilet, AirConditioning, PetsCarriage, Press, HygienicKit, InfotainmentService) |
| `carrier` | `str \| None` | код перевозчика (напр. `ФПК`) |
| `carrier_display_name` | `str \| None` | отображаемое название перевозчика |
| `numeration` | `str \| None` | нумерация вагона (`FromHead` — с головы, `FromTail` — с хвоста) |
| `direction` | `str \| None` | направление вагона |
| `train_number` | `str \| None` | номер поезда |
| `has_images` | `bool \| None` | есть ли изображения вагона |
| `is_two_storey` | `bool` (raw) | двухэтажный ли вагон |
| `pet_transportation_short_description` | `str` (raw) | краткое описание правил перевозки животных |
| `pet_transportation_full_description` | `str` (raw) | полное описание правил перевозки животных |

#### `get_car_scheme` — схема вагона

| Поле | Тип | Описание |
|------|------|-----------|
| `scheme_id` | `int \| None` | ID схемы |
| `car_sub_type` | `str \| None` | подтип вагона |
| `train_number` | `str \| None` | номер поезда |
| `car_number` | `str \| None` | номер вагона |
| `carrier` | `str \| None` | перевозчик |
| `service_class` | `str \| None` | класс обслуживания |
| `direction` | `str \| None` | направление |
| `first_storey` | `str \| None` | первый этаж (SVG-разметка с номерами мест) |
| `second_storey` | `str \| None` | второй этаж (SVG-разметка) |
| `mobile_first_storey` | `str \| None` | мобильная версия первого этажа |
| `mobile_second_storey` | `str \| None` | мобильная версия второго этажа |
| `start_date` | `str \| None` | начало действия схемы |
| `end_date` | `str \| None` | конец действия схемы |

#### `get_car_images` — изображения вагона

| Поле | Тип | Описание |
|------|------|-----------|
| `scheme_id` | `int \| None` | ID схемы |
| `car_sub_type` | `str \| None` | подтип вагона |
| `images.image_id` | `int \| None` | ID изображения |
| `images.title_ru` | `str \| None` | название изображения (рус.) |
| `images.title_en` | `str \| None` | название изображения (англ.) |
| `images.preview` | `str \| None` | URL превью-изображения |
| `images.content` | `str \| None` | URL полноразмерного изображения |
| `images.sequence_number` | `int \| None` | порядковый номер |

#### `get_minimal_prices` — минимальные цены по диапазону дат

| Поле | Тип | Описание |
|------|------|-----------|
| `origin_code` | `str` | код станции отправления |
| `destination_code` | `str` | код станции прибытия |
| `prices[].date` | `str` | дата |
| `prices[].min_price` | `float \| None` | минимальная цена (₽) |
| `prices[].disabled_place_min_price` | `float \| None` | минимальная цена для места инвалида (₽) |
| `prices[].carriers` | `list[dict]` | перевозчики с ценами |

#### `get_train_availability` — доступность поезда по датам

| Поле | Тип | Описание |
|------|------|-----------|
| `origin_code` | `str` | код станции отправления |
| `destination_code` | `str` | код станции прибытия |
| `items[].date` | `str` | дата |

#### `get_route_stations` — маршрут поезда с остановками

| Поле | Тип | Описание |
|------|------|-----------|
| `train_number` | `str \| None` | номер поезда |
| `route_name` | `str \| None` | название маршрута |
| `origin_name` | `str \| None` | начальная станция |
| `destination_name` | `str \| None` | конечная станция |
| `stations[].name` | `str \| None` | название станции |
| `stations[].code` | `str \| None` | код станции |
| `stations[].city_name` | `str \| None` | город |
| `stations[].arrival_time` | `str \| None` | время прибытия |
| `stations[].departure_time` | `str \| None` | время отправления |
| `stations[].local_arrival_time` | `str \| None` | локальное время прибытия |
| `stations[].local_departure_time` | `str \| None` | локальное время отправления |
| `stations[].stop_duration` | `float \| None` | длительность стоянки (минуты) |
| `stations[].distance` | `int \| None` | расстояние от начала маршрута (км) |
| `stations[].days_from_origin` | `int \| None` | дней в пути от начала |
| `stations[].time_zone_difference` | `int \| None` | разница часовых поясов |
| `stations[].actual_movement` | `bool \| None` | фактическое движение |
| `stations[].is_cutaway_station` | `bool \| None` | отцепная ли станция |
| `stations[].time_description` | `str \| None` | описание времени стоянки |

#### `find_stations` — поиск станций по названию

| Поле | Тип | Описание |
|------|------|-----------|
| `name` | `str` | название станции |
| `code` | `str` | код станции (напр. `2020000`) |
| `node_id` | `str \| None` | внутренний ID узла |
| `node_type` | `str \| None` | тип узла (`city` / `train`) |
| `transport_type` | `str \| None` | тип транспорта |
| `region` | `str \| None` | регион (напр. «Саратовская Область, Российская Федерация») |

#### `resolve_station_code` — получение кода станции

| Поле | Тип | Описание |
|------|------|-----------|
| (возвращаемое значение) | `str` | код станции |

---

## 2. Tutu MCP (mcp.tutu.ru)

**Источник:** MCP-сервер Туту.ру
**Способ получения:** MCP (Model Context Protocol) через Streamable HTTP, без авторизации. Endpoint: `https://mcp.tutu.ru/mcp`

### Поисковые инструменты

#### `search_avia` — поиск авиабилетов

| Поле | Тип | Описание |
|------|------|-----------|
| `offer.id` | `str` | ID предложения |
| `offer.status` | `str` | статус предложения |
| `offer.best_offer.price.total` | `float` | полная стоимость билета (₽) |
| `offer.best_offer.price.currency` | `str` | валюта цены |
| `offer.best_offer.price.fare_family` | `str` | тарифное семейство |
| `offer.best_offer.price.baggage` | `str` | информация о багаже |
| `offer.best_offer.price.refund` | `str` | условия возврата |
| `offer.best_offer.price.fare_conditions` | `str` | условия тарифа |
| `offer.best_offer.service_class` | `str` | класс обслуживания (ECONOMIC / PREMIUM_ECONOMY / BUSINESS / FIRST) |
| `offer.legs[].duration` | `int` | длительность рейса (минуты) |
| `offer.legs[].segments[].from.name` | `str` | название аэропорта отправления |
| `offer.legs[].segments[].from.city_name` | `str` | город отправления |
| `offer.legs[].segments[].from.city_id` | `int` | ID города отправления |
| `offer.legs[].segments[].from.iata_code` | `str` | IATA-код аэропорта отправления |
| `offer.legs[].segments[].to.name` | `str` | название аэропорта прибытия |
| `offer.legs[].segments[].to.city_name` | `str` | город прибытия |
| `offer.legs[].segments[].to.city_id` | `int` | ID города прибытия |
| `offer.legs[].segments[].to.iata_code` | `str` | IATA-код аэропорта прибытия |
| `offer.legs[].segments[].departure_at` | `str` | дата и время отправления (ISO 8601) |
| `offer.legs[].segments[].arrival_at` | `str` | дата и время прибытия (ISO 8601) |
| `offer.legs[].segments[].airline_code` | `str` | код авиакомпании |
| `offer.legs[].segments[].airline_name` | `str` | название авиакомпании |
| `offer.legs[].segments[].flight_no` | `str` | номер рейса |
| `offer.legs[].segments[].aircraft` | `str` | тип самолёта |
| `offer.variants[].offer_hash` | `dict` | хеш для покупки конкретного тарифа |
| `offer.variants[].service_class` | `str` | класс обслуживания варианта |
| `offer.variants[].price.total` | `float` | цена варианта (₽) |
| `offer.variants[].fare_family_name` | `str` | название тарифного семейства |
| `offer.search_results_url` | `str` | URL страницы результатов поиска на tutu.ru |
| `offer.checkout_ref` | `object` | объект для формирования ссылки на покупку |
| `meta.carriers_available` | `list` | доступные для фильтрации авиакомпании |
| `meta.total_offers` | `int` | общее количество предложений |

#### `search_rail` — поиск ж/д билетов

| Поле | Тип | Описание |
|------|------|-----------|
| `offer.id` | `str` | ID предложения |
| `offer.status` | `str` | статус предложения |
| `offer.best_offer.price.total` | `float` | стоимость билета (₽) |
| `offer.best_offer.price.currency` | `str` | валюта цены |
| `offer.best_offer.service_class` | `str` | класс обслуживания (напр. `2Ф`, `2Э`, `3Э`) |
| `offer.best_offer.car_type` | `str` | тип вагона (КУПЕ / ПЛАЦ / СВ / СИДЯЧИЙ) |
| `offer.legs[].segments[].from.name` | `str` | название вокзала отправления |
| `offer.legs[].segments[].from.city_name` | `str` | город отправления |
| `offer.legs[].segments[].from.geo_point_id` | `int` | ID геоточки отправления |
| `offer.legs[].segments[].from.code` | `str` | код станции отправления |
| `offer.legs[].segments[].to.name` | `str` | название вокзала прибытия |
| `offer.legs[].segments[].to.city_name` | `str` | город прибытия |
| `offer.legs[].segments[].to.geo_point_id` | `int` | ID геоточки прибытия |
| `offer.legs[].segments[].to.code` | `str` | код станции прибытия |
| `offer.legs[].segments[].departure_at` | `str` | дата и время отправления |
| `offer.legs[].segments[].arrival_at` | `str` | дата и время прибытия |
| `offer.legs[].segments[].duration` | `int` | длительность поездки (минуты) |
| `offer.legs[].segments[].train_number` | `str` | номер поезда (напр. `022А`) |
| `offer.legs[].segments[].train_name` | `str` | название поезда |
| `offer.legs[].segments[].carrier_name` | `str` | название перевозчика |
| `offer.legs[].segments[].voyage_no` | `str` | номер рейса для отображения |
| `offer.seatmap_available` | `bool` | доступна ли схема мест |
| `offer.checkout_ref` | `object` | объект для формирования ссылки на покупку |
| `meta.carriers_available` | `list` | доступные для фильтрации перевозчики |
| `meta.total_offers` | `int` | общее количество предложений |

#### `get_rail_seatmap` — схема мест в вагоне поезда

| Поле | Тип | Описание |
|------|------|-----------|
| `cars[].car_number` | `str` | номер вагона |
| `cars[].car_type` | `str` | тип вагона (КУПЕ / ПЛАЦ / СВ / СИДЯЧИЙ) |
| `cars[].car_type_code` | `str` | код типа вагона |
| `cars[].service_class` | `str` | класс обслуживания |
| `cars[].numeration` | `str` | нумерация (с головы / с хвоста) |
| `cars[].seats[].number` | `str` | номер места |
| `cars[].seats[].status` | `str` | статус места (свободно/занято) |
| `cars[].seats[].type` | `str` | тип места (нижнее / верхнее / боковое) |
| `cars[].seats[].price` | `float` | цена места (₽) |
| `cars[].seats[].gender` | `str` | пол купе (М/Ж/Смешанное) |
| `cars[].fares[].fare_type` | `str` | тип тарифа (REFUNDABLE / NON_REFUNDABLE) |
| `cars[].fares[].price` | `float` | цена по тарифу (₽) |
| `cars[].fares[].fare_name` | `str` | название тарифа |
| `cars[].group_index` | `int` | индекс группы для сопоставления с тарифами |

#### `search_hotels` — поиск отелей

| Поле | Тип | Описание |
|------|------|-----------|
| `offer.id` | `str` | ID предложения |
| `offer.status` | `str` | статус |
| `offer.hotel.name` | `str` | название отеля |
| `offer.hotel.stars` | `int` | количество звёзд |
| `offer.hotel.alias` | `str` | алиас отеля для URL |
| `offer.hotel.address` | `str` | адрес отеля |
| `offer.hotel.city_name` | `str` | город |
| `offer.hotel.geo_id` | `int` | ID отеля в гео-системе |
| `offer.best_offer.price.total` | `float` | полная стоимость (₽) |
| `offer.best_offer.price.currency` | `str` | валюта |
| `offer.best_offer.price.per_night` | `float` | цена за ночь (₽) |
| `offer.best_offer.room_name` | `str` | название номера |
| `offer.best_offer.board_name` | `str` | тип питания |
| `offer.best_offer.check_in` | `str` | дата заезда |
| `offer.best_offer.check_out` | `str` | дата выезда |
| `offer.best_offer.nights` | `int` | количество ночей |
| `offer.best_offer.review_score` | `float` | оценка по отзывам |
| `offer.best_offer.review_count` | `int` | количество отзывов |
| `offer.best_offer.checkout_url` | `str` | URL страницы отеля для бронирования |
| `offer.best_offer.offerpack_hash` | `str` | хеш пакета предложения |
| `offer.best_offer.amenities` | `list[str]` | удобства |
| `offer.checkout_ref` | `object` | объект для формирования ссылки на бронирование |
| `meta.total_offers` | `int` | общее количество предложений |

#### `get_offer_details` — детали предложения (hotel/avia/rail/bus/etrain)

**Для отелей:**

| Поле | Тип | Описание |
|------|------|-----------|
| `hotel.name` | `str` | название отеля |
| `hotel.stars` | `int` | звёзды |
| `hotel.address` | `str` | адрес |
| `hotel.description` | `str` | описание отеля |
| `hotel.photos[].url` | `str` | URL фотографии |
| `hotel.photos[].caption` | `str` | подпись к фото |
| `hotel.amenities[]` | `list[str]` | список удобств отеля |
| `hotel.reviews[].score` | `float` | оценка |
| `hotel.reviews[].text` | `str` | текст отзыва |
| `hotel.reviews[].author` | `str` | автор отзыва |
| `hotel.reviews[].date` | `str` | дата отзыва |
| `rooms[].name` | `str` | название номера |
| `rooms[].description` | `str` | описание номера |
| `rooms[].photos[].url` | `str` | фото номера |
| `rooms[].square` | `float` | площадь номера (м²) |
| `rooms[].max_guests` | `int` | максимальное количество гостей |
| `rooms[].bed_type` | `str` | тип кровати |
| `rooms[].view` | `str` | вид из окна |
| `rooms[].rates[].name` | `str` | название тарифа |
| `rooms[].rates[].board_name` | `str` | тип питания |
| `rooms[].rates[].price.total` | `float` | стоимость (₽) |
| `rooms[].rates[].cancellation` | `str` | условия отмены |
| `rooms[].rates[].offerpack_hash` | `str` | хеш для бронирования |

**Для транспорта:**

| Поле | Тип | Описание |
|------|------|-----------|
| `segments[].from.name` | `str` | пункт отправления |
| `segments[].to.name` | `str` | пункт прибытия |
| `segments[].departure_at` | `str` | время отправления |
| `segments[].arrival_at` | `str` | время прибытия |
| `segments[].duration` | `int` | длительность (минуты) |
| `variants[].price` | `float` | цена варианта |
| `seat_selection.available_seat_ids` | `list` | доступные для выбора места (для bus) |

**Для автобусов (bus):** дополнительно:
- `amenities` — удобства автобуса
- `refund` — условия возврата
- `luggage` — информация о багаже

#### `search_bus` — поиск автобусных билетов

| Поле | Тип | Описание |
|------|------|-----------|
| `offer.best_offer.price.total` | `float` | стоимость билета (₽) |
| `offer.best_offer.price.currency` | `str` | валюта |
| `offer.legs[].segments[].from.name` | `str` | автостанция отправления |
| `offer.legs[].segments[].from.city_name` | `str` | город отправления |
| `offer.legs[].segments[].to.name` | `str` | автостанция прибытия |
| `offer.legs[].segments[].to.city_name` | `str` | город прибытия |
| `offer.legs[].segments[].departure_at` | `str` | время отправления |
| `offer.legs[].segments[].arrival_at` | `str` | время прибытия |
| `offer.legs[].segments[].duration` | `int` | длительность поездки (минуты) |
| `offer.legs[].segments[].carrier_name` | `str` | название перевозчика |
| `offer.legs[].segments[].bus_model` | `str` | модель автобуса |
| `offer.checkout_ref` | `object` | объект для формирования ссылки на покупку |

#### `search_etrain` — поиск электричек

| Поле | Тип | Описание |
|------|------|-----------|
| `offer.best_offer.price.total` | `float` | стоимость билета (₽) |
| `offer.best_offer.price.currency` | `str` | валюта |
| `offer.legs[].segments[].from.name` | `str` | станция отправления |
| `offer.legs[].segments[].from.city_name` | `str` | город отправления |
| `offer.legs[].segments[].to.name` | `str` | станция прибытия |
| `offer.legs[].segments[].to.city_name` | `str` | город прибытия |
| `offer.legs[].segments[].departure_at` | `str` | время отправления |
| `offer.legs[].segments[].arrival_at` | `str` | время прибытия |
| `offer.legs[].segments[].duration` | `int` | длительность (минуты) |
| `offer.legs[].segments[].train_number` | `str` | номер электрички |
| `offer.legs[].segments[].carrier_name` | `str` | перевозчик (напр. ЦППК, МТППК) |
| `offer.legs[].vehicle_meta.consist_type` | `str` | тип состава |
| `offer.checkout_ref` | `object` | объект для формирования ссылки |

#### `search_multitransport` — мультимодальный поиск (сравнение avia/rail/bus/etrain)

| Поле | Тип | Описание |
|------|------|-----------|
| `variants[].transport` | `str` | вид транспорта (avia / rail / bus / etrain) |
| `variants[].offers[]` | `list` | список предложений в том же формате, что и в per-mode поиске |
| `variants[].error` | `str \| null` | ошибка для данного вида транспорта, если не удалось найти |

#### `create_checkout_link` — формирование ссылки на покупку

| Поле | Тип | Описание |
|------|------|-----------|
| `checkout_url` | `str` | прямая ссылка на страницу покупки билета или выбора места |
| `kind` | `str` | тип ссылки (`deeplink` / `checkout_deeplink` / `search_redirect` / `order_url` / `seats_url` / `hotel_page`) |
| `search_results_url` | `str \| None` | URL страницы результатов поиска (для avia) |
| `fallback_url` | `str \| None` | fallback-ссылка (для отелей) |

#### `fetch_resource` — служебные ресурсы сервера

| Поле | Тип | Описание |
|------|------|-----------|
| `tutu://help/overview` | `text/markdown` | общая справка по использованию MCP-сервера |
| `tutu://geo` | `text/json` | справочник ID городов и геоточек |
| `tutu://status` | `text/json` | статус сервера и upstream-сервисов |
| `tutu://special-offers` | `text/json` | специальные предложения (экспериментальный) |
| `tutu://amenities/dictionary` | `text/json` | словарь кодов удобств |
| `tutu://version` | `text/json` | версия сервера |
| `tutu://debug/memory` | `text/json` | диагностика памяти |

#### Общие фильтры и параметры поиска

Для транспортных инструментов (`search_avia`, `search_rail`, `search_bus`, `search_etrain`, `search_multitransport`):

| Параметр | Описание |
|----------|----------|
| `direct_only` | только прямые рейсы |
| `price_max` | максимальная цена |
| `carriers` | фильтр по перевозчикам/авиакомпаниям |
| `sort` | сортировка результатов |
| `adults` / `children` / `infants` | количество пассажиров |
| `page` / `per_page` | пагинация |

Для отелей (`search_hotels`):

| Параметр | Описание |
|----------|----------|
| `check_in` / `check_out` | даты заезда/выезда |
| `adults` / `children_ages` | гости |
| `stars_min` / `stars_max` | фильтр по звёздам |
| `price_max` | максимальная цена |
| `page` / `per_page` | пагинация |

#### `plan_trip` — промпт для комплексного планирования поездки

Оркестрирует `search_multitransport` + `search_hotels` для подбора транспорта и отеля в рамках бюджета.

---

## 3. Yandex Travel Partners API (hotels)

**Источник:** Яндекс Путешествия (Travel Partners API)  
**Способ получения:** REST API через HTTPS. **Требуется OAuth-токен** (регистрация партнёра).  
**Base URL:** `https://whitelabel.travel.yandex-net.ru/hotels/`  
**Обязательный заголовок:** `Authorization: OAuth <токен>`

### Методы

#### `GET hotels/suggest` — подсказки по регионам и отелям

| Параметр | Тип | Описание |
|----------|-----|---------|
| `query`* | `str` | Поисковый запрос (до 200 символов) |
| `region_limit` | `int` | Макс. подсказок по регионам (до 25) |
| `hotel_limit` | `int` | Макс. подсказок по отелям (до 25) |

**Ответ:**
| Поле | Тип | Описание |
|------|-----|---------|
| `regions[].geo_id` | `int` | ID региона |
| `regions[].type` | `str` | Тип: COUNTRY / REGION / CITY / VILLAGE / DISTRICT |
| `regions[].name` | `str` | Название региона |
| `regions[].description` | `str` | Описание (напр. «Свердловская область») |
| `hotels[].hotel_id` | `int` | ID отеля |
| `hotels[].name` | `str` | Название отеля |
| `hotels[].description` | `str` | Описание (адрес, расположение) |

---

#### `GET hotels/search` — поиск отелей с предложениями

| Параметр | Тип | Описание |
|----------|-----|---------|
| `geo_id`* | `int` | ID региона |
| `checkin_date`* | `date` | Дата заезда YYYY-MM-DD |
| `checkout_date`* | `date` | Дата выезда YYYY-MM-DD |
| `adults`* | `int` | Количество взрослых |
| `children_ages` | `list[int]` | Возрасты детей через запятую |
| `order_by` | `enum` | relevance-desc / rating-desc / price-asc / price-desc |
| `hotel_id` | `str` | Конкретный отель — будет первым в выдаче |
| `page_limit` | `int` | Размер страницы (по умол. 10, макс. 50) |
| `page_token` | `str` | Токен следующей страницы |
| `images_limit` | `int` | Макс. фото в сниппете (по умол. 10, макс. 20) |
| `bbox` | `str` | Область карты: `minLon,minLat~maxLon,maxLat` |
| `min_price` / `max_price` | `int` | Фильтр цены за ночь (₽) |
| `meal_type` | `str` | Тип питания: RO\|BB\|HB\|FB\|AI |
| `stars` | `str` | Звёздность: `1|2|3|4|5` |
| `min_rating` | `float` | Мин. рейтинг: 3 / 4 / 4.5 |
| `accomm_type` | `str` | Тип: hotel\|hostel\|camping\|sanatorium\|apartment и др. |
| `free_cancellation` | `bool` | Только с бесплатной отменой |
| `nearby_sea` / `nearby_park` / `nearby_airport` | `bool` | Рядом с морем/парком/аэропортом |
| `wi_fi` / `air_conditioning` / `pool` / `car_park` / `spa` / `pets` / `sauna` / `bathhouse` / `restaurant` / `cafe` / `gym` / `transfer` | `bool` | Фильтры по удобствам |
| `is_corporate` | `bool` | Только с корпоративными тарифами |

**Ответ → `hotel_snippets[]`:**
| Поле | Тип | Описание |
|------|-----|---------|
| `hotel_id` | `str` | ID отеля |
| `name` | `str` | Название |
| `location.country_name` | `str` | Страна |
| `location.settlement.type` | `str` | CITY / VILLAGE |
| `location.settlement.name` | `str` | Название населённого пункта |
| `location.address` | `str` | Адрес |
| `location.lon` / `location.lat` | `double` | Координаты |
| `stars` | `int` | 1–5 |
| `rating` | `str` | Рейтинг (1.0–5.0) |
| `total_review_count` | `int` | Количество отзывов |
| `total_image_count` | `int` | Количество фото |
| `images[].url_template` | `str` | Шаблон URL (подставить `%s` = код размера) |
| `images[].sizes[].size` | `str` | Код размера (XXXS, L, XXL и т.д.) |
| `images[].sizes[].height` / `width` | `int` | Размеры в px |
| `top_offers[].name` | `str` | Название предложения |
| `top_offers[].price.value` | `int` | Цена (₽) |
| `top_offers[].price.currency` | `str` | Валюта (RUB) |
| `top_offers[].meal_type.id` | `str` | RO / BB / HB / FB / AI |
| `top_offers[].meal_type.name` | `str` | Название типа питания |
| `top_offers[].cancellation.refund_type` | `str` | FULLY_REFUNDABLE / REFUNDABLE_WITH_PENALTY / NON_REFUNDABLE |
| `top_offers[].cancellation.refund_rules[]` | `obj` | Правила отмены с периодами и штрафами |
| `top_offers[].discount.strikethrough_price` | `int` | Цена до скидки |
| `top_offers[].discount.percent` | `int` | Процент скидки |
| `top_offers[].discount.reason` | `str` | Причина скидки |
| `top_offers[].is_corporate` | `bool` | Корпоративный тариф |
| `landing_url` | `str` | Ссылка на страницу отеля |
| `next_page_token` | `str` | Токен для следующей страницы |
| `complete` | `bool` | Поиск завершён (polling) |

---

#### `GET hotels/hotel` — детальная информация об отеле

| Параметр | Тип | Описание |
|----------|-----|---------|
| `hotel_id`* | `str` | ID отеля |

**Ответ:**
| Поле | Тип | Описание |
|------|-----|---------|
| `name` | `str` | Название |
| `stars` | `int` | 1–5 |
| `location.*` | — | Страна, нас. пункт, адрес, координаты |
| `check_in.from` / `check_in.until` | `str` | Время заезда (hh:mm) |
| `check_out.from` / `check_out.until` | `str` | Время выезда (hh:mm) |
| `total_review_count` | `int` | Количество отзывов |
| `total_image_count` | `int` | Количество фото |
| `ratings.rating` | `str` | Общий рейтинг (1.0–5.0) |
| `ratings.teaser` | `str` | Текстовая оценка (напр. «100% гостей понравилось питание») |
| `ratings.feature_ratings[].name` | `str` | Критерий (Питание, Сервис, Расположение...) |
| `ratings.feature_ratings[].positive_percent` | `int` | % позитивных оценок |
| `location_features[].type` | `str` | METRO / STATION / OTHER |
| `location_features[].name` | `str` | Название станции/ориентира |
| `location_features[].distance_meters` | `int` | Расстояние в метрах |
| `location_features[].metro_line.name` | `str` | Название линии метро |
| `location_features[].metro_line.color` | `str` | Цвет линии (HTML) |
| `amenities.groups[].name` | `str` | Название группы удобств |
| `amenities.groups[].amenities[].id` | `str` | ID удобства |
| `amenities.groups[].amenities[].name` | `str` | Название удобства |
| `amenities.groups[].amenities[].is_important` | `bool` | Основное удобство |

---

#### `GET hotels/hotel/offers` — номера и предложения отеля

| Параметр | Тип | Описание |
|----------|-----|---------|
| `hotel_id`* | `str` | ID отеля |
| `checkin_date`* | `date` | Дата заезда |
| `checkout_date`* | `date` | Дата выезда |
| `adults`* | `int` | Количество взрослых |
| `children_ages` | `list[int]` | Возрасты детей |

**Ответ → `rooms[]`:**
| Поле | Тип | Описание |
|------|-----|---------|
| `name` | `str` | Название номера |
| `description` | `str` | Описание номера |
| `area.value` | `int` | Площадь |
| `area.unit` | `str` | SQUARE_METERS |
| `bed_groups[].configuration[]` | `obj` | Конфигурации кроватей |
| `bed_groups[].configuration[].bed_type` | `str` | SINGLE / DOUBLE |
| `bed_groups[].configuration[].name_initial_form` | `str` | Название кровати (ед. ч.) |
| `bed_groups[].configuration[].name_inflected_form` | `str` | Название кровати (мн. ч.) |
| `bed_groups[].configuration[].quantity` | `int` | Количество кроватей |
| `amenities.groups[]` | `obj` | Удобства номера |
| `images[]` | `obj` | Фото номера |
| `offers[].id` | `str` | ID предложения |
| `offers[].name` | `str` | Название предложения |
| `offers[].price.value` | `int` | Цена (₽) |
| `offers[].price.currency` | `str` | RUB |
| `offers[].meal_type.id` / `meal_type.name` | `str` | Тип питания |
| `offers[].cancellation.*` | `obj` | Правила отмены |
| `offers[].discount.*` | `obj` | Скидка (strikethrough_price, percent, reason) |
| `offers[].booking_url` | `str` | Ссылка на бронирование |

---

#### `GET hotels/hotel/reviews` — отзывы об отеле

| Параметр | Тип | Описание |
|----------|-----|---------|
| `hotel_id`* | `str` | ID отеля |
| `page_limit` | `int` | Размер страницы |
| `page_token` | `str` | Токен страницы |
| `order_by` | `str` | Сортировка отзывов |

#### `GET hotels/hotel/images` — изображения отеля

| Параметр | Тип | Описание |
|----------|-----|---------|
| `hotel_id`* | `str` | ID отеля |
| `page_limit` | `int` | Размер страницы |
| `page_token` | `str` | Токен страницы |

---

### Особенности Yandex Travel Partners API

- **Авторизация:** OAuth-токен обязателен (требуется регистрация партнёра в Яндекс Дистрибуции)
- **Paging:** через `page_token` / `next_page_token`
- **Polling:** некоторые методы возвращают `"complete": false` — нужно повторять запрос
- **Фильтры:** богатый набор фильтров по удобствам, типу размещения, питанию, расположению
- **Данные:** названия номеров, конфигурации кроватей, планировка, отзывы с разбивкой по критериям, фото с разными размерами
- **Бронирование:** ссылки `booking_url` и `landing_url` ведут на страницы Яндекс Путешествий
- **Сравнение с Tutu:** Tutu MCP уже включает `search_hotels` без OAuth, но с меньшей детализацией по кроватям и отзывам

---

## 4. Skyscanner API (flights, hotels, car hire)

**Источник:** Skyscanner Partners API  
**Способ получения:** REST API через HTTPS. **Требуется API-ключ** (заявка партнёра).  
**Base URL:** `https://partners.api.skyscanner.net/apiservices/v3/`  
**Авторизация:** заголовок `x-api-key: <ключ>`  
**Протокол:** `create`/`poll` — сначала создаётся сессия поиска, затем опрашивается до готовности  
**Покрытие:** 52 рынка, 30 языков, 1300+ поставщиков

### Доступные API

| API | Описание | Доступ |
|-----|----------|--------|
| **Flights Live Prices** | Реальные цены на авиабилеты | create + poll |
| **Flights Indicative Prices** | Оценочные цены для SEO/лендингов | REST |
| **Car Hire Live Prices** | Реальные цены на аренду авто | create + poll |
| **Car Hire Indicative Prices** | Оценочные цены аренды авто | REST |
| **Car Hire Agents** | Детали агентов проката (название, рейтинг) | REST |
| **Hotels Live Prices** | Реальные цены на отели | create + poll |
| **Hotels Indicative Prices** | Оценочные цены отелей | REST |
| **Hotels Content** | Статика: удобства, политики, фото | REST |
| **Hotels Reviews** | Отзывы путешественников | REST |
| **Geo** | Аэропорты, города, страны | REST |
| **Culture** | Рынки, локали, валюты | REST |
| **Autosuggest** | Подсказки мест (по названию, IP, координатам) | REST |
| **Carriers** | Детали авиакомпаний | REST |
| **Affiliates Link** | Deep-ссылки на Skyscanner | REST |
| **MCP Server** | MCP для AI-продуктов | По заявке (case-by-case) |

---

### Flights Live Prices API

#### `POST /flights/live/search/create` — создание поиска

| Поле | Тип | Описание |
|------|-----|---------|
| `query.market`* | `str` | Рынок (напр. `UK`) |
| `query.locale`* | `str` | Локаль (напр. `en-GB`) |
| `query.currency`* | `str` | Валюта цен (напр. `GBP`) |
| `query.queryLegs`* | `array` | Направления поиска (до 6) |
| `query.adults`* | `int` | Количество взрослых (1–8) |
| `query.childrenAges` | `list[int]` | Возрасты детей (0–8) |
| `query.cabinClass` | `enum` | ECONOMY / PREMIUM_ECONOMY / BUSINESS / FIRST |
| `query.includedCarriersIds` | `list[str]` | Только эти авиакомпании |
| `query.excludedCarriersIds` | `list[str]` | Исключить авиакомпании |
| `query.includedAgentsIds` | `list[str]` | Только эти OTA |
| `query.excludedAgentsIds` | `list[str]` | Исключить OTA |
| `query.nearbyAirports` | `bool` | Включить соседние аэропорты отправления |
| `query.includeSustainabilityData` | `bool` | Включить данные о выбросах CO₂ |

**`queryLeg` — одно направление:**
| Поле | Тип | Описание |
|------|-----|---------|
| `originPlaceId.iata` или `entityId` | `str`/`int` | Аэропорт/город отправления |
| `destinationPlaceId.iata` или `entityId` | `str`/`int` | Аэропорт/город прибытия |
| `date.year` / `month` / `day` | `int` | Дата вылета |

#### `POST /flights/live/search/poll/{sessionToken}` — получение результатов

Возвращает полный список рейсов. `sessionToken` из ответа `/create`, живёт ~1 час.

**Ответ:**
| Поле | Тип | Описание |
|------|-----|---------|
| `Status` | `str` | `running` / `completed` |
| `Action` | `str` | Как обрабатывать `SearchResults` |
| `Itineraries[]` | `obj` | Маршруты с `deepLink` на бронирование |
| `Itineraries[].legs[]` | `obj` | Плечи маршрута (1 — one-way, 2 — return) |
| `legs[].segments[]` | `obj` | Сегменты плеча (1 — прямой, >1 — с пересадками) |
| `segments[].originPlaceId` / `destinationPlaceId` | `str` | Аэропорты сегмента |
| `segments[].departureDateTime` / `arrivalDateTime` | `str` | Время вылета/прилёта (ISO 8601) |
| `segments[].durationInMinutes` | `int` | Длительность в минутах |
| `segments[].marketingCarrierId` | `str` | Код авиакомпании |
| `segments[].operatingCarrierId` | `str` | Код оперирующей авиакомпании |
| `segments[].flightNumber` | `str` | Номер рейса |
| `Places[]` | `obj` | Справочник аэропортов/городов (IATA, название, город, страна) |
| `Carriers[]` | `obj` | Справочник авиакомпаний (IATA, название, логотип) |
| `Agents[]` | `obj` | Справочник OTA (ID, название, логотип) |
| `Stats.minPrice` / `minDuration` / `maxDuration` | `int`/`str` | Метрики выдачи |
| `Stats.stops.direct` / `oneStop` / `twoPlusStops` | `int` | Количество по пересадкам |
| `SortingOptions` | `obj` | Лучшее/самое дешёвое/самое быстрое |

**Multi-City:** поддерживается до 6 плеч в возрастающем порядке дат.

---

### Hotels Live Prices API

Аналогичный create/poll workflow для поиска отелей с реальными ценами.

### Hotels Content API

Статическая информация об отеле: удобства, политики заезда/выезда, фото номеров, описание.

### Hotels Reviews API

Отзывы путешественников с рейтингами и текстами.

---

### Skyscanner MCP Server

Запущен для поддержки AI-powered travel-продуктов. Доступен **по заявке** (case-by-case), через account manager или `partners@skyscanner.net`. Детали протокола не раскрыты публично.

---

### Сравнение с другими сервисами

| Критерий | Skyscanner | Tutu MCP | RZD API | Yandex Travel |
|----------|-----------|----------|---------|---------------|
| **Поезда** | Нет | ✅ (search_rail) | ✅ | Нет |
| **Авиабилеты** | ✅ (основной профиль) | ✅ (search_avia) | Нет | Нет |
| **Отели** | ✅ (live + content + reviews) | ✅ (search_hotels) | Нет | ✅ (богаче Tutu) |
| **Аренда авто** | ✅ | Нет | Нет | Нет |
| **Автобусы** | Нет | ✅ (search_bus) | Нет | Нет |
| **Электрички** | Нет | ✅ (search_etrain) | Нет | Нет |
| **Авторизация** | API-ключ (заявка) | Без авторизации | Без авторизации | OAuth-токен (партнёр) |
| **Протокол** | REST (create/poll) | MCP (JSON-RPC) | REST (rzd-api) | REST |
| **MCP-сервер** | ✅ (по заявке) | ✅ (публичный) | Нет | Нет |
| **Геопокрытие** | Глобальное (52 рынка) | РФ + СНГ | РФ | РФ |

### Вывод

Skyscanner **не добавляет** ценности для поездов (основной кейс проекта) — его профиль: авиабилеты и аренда авто. Для отелей пересекается с Tutu MCP (бесплатный, без ключей) и Yandex Travel (богаче по детализации). Для авиабилетов — сильнейший источник, но требует партнёрской заявки и API-ключа. MCP-сервер Skyscanner доступен только по индивидуальному согласованию.

---

## 5. Travelline API (hotels / PMS)

**Источник:** TravelLine Partner APIs  
**Способ получения:** REST API через HTTPS. **Требуется OAuth2.0 токен** (client_credentials flow).  
**Base URL:** `https://partner.tlintegration.com/api/`  
**Auth endpoint:** `POST https://partner.tlintegration.com/auth/token`  
**Авторизация:** `Authorization: Bearer <JWT access_token>`  
**Время жизни токена:** 15 минут (без refresh)  
**Применение:** полноценная PMS-интеграция для средств размещения — от поиска до заселения и оплаты

### Особенности
- Нет данных по билетам (поезда/авиа/автобусы отсутствуют)
- **Исключительно отели/средства размещения**
- Самый глубокий API из всех рассмотренных: от контента до PMS (property management system)
- Подходит как для поиска/бронирования, так и для управления отелем (заселение, оплата, аналитика)

### Доступные API

| API | Описание | Ключевые методы |
|-----|----------|----------------|
| **Content API** | Описание отеля: фото, номера, тарифы, услуги, удобства | `GET /v1/properties`, `GET /v1/properties/{id}` |
| **Search API** | Поиск вариантов размещения по цене/условиям | `POST /v1/search`, `GET /v1/properties/{id}/search` |
| **Read Reservation API** | Сводки и детали бронирований | `GET /v1/reservations`, `GET /v1/reservations/{id}` |
| **PMS API** | Управление: инвентарь, гости, заселение, оплата, возвраты | Гости, бронирования, комнаты, платежи |
| **PMS Analytics API** | Статистика загрузки отеля по дням | `GET /v1/analytics/occupancy` |
| **PMS Integration Storage API** | Бронирования и блоки инвентаря через вебхуки | `GET /v1/bookings/{id}`, `POST /v1/inventory` |
| **ReferenceData API** | Справочники: корп. клиенты, способы оплаты, типы удобств | `GET /v1/payment-methods`, `GET /v1/property-kinds` |
| **CancellationRules** | Правила отмены с временными штрафами | `GET /v1/properties/{id}/cancellation-rules` |
| **ExtraStayRules** | Ранний заезд / поздний выезд | `GET /v1/properties/{id}/extra-stay-rules` |
| **Public Reviews API** | Отзывы с рейтингами и категориями | `GET /v1/reviews`, `GET /v1/reviews/stats` |

---

### Content API — описание отеля

#### `GET /v1/properties` — список всех средств размещения

| Параметр | Тип | Описание |
|----------|-----|---------|
| `since` | `str` | Курсор пагинации (из `next` предыдущего ответа) |
| `count` | `int` | Элементов в ответе (макс. 200, умолч. 200) |
| `include` | `str` | `All` — полная информация; пусто — только ID |
| `languageCode` | `str` | Язык (умолч. язык отеля), `en` для английского |

#### `GET /v1/properties/{propertyId}` — информация о конкретном отеле

**Ответ (основные поля):**
| Поле | Тип | Описание |
|------|-----|---------|
| `id` | `str` | ID средства размещения |
| `name` | `str` | Название |
| `description` | `str` | Описание |
| `stars` | `int` | Звёздность (1–5) |
| `images[].url` | `str` | URL фото |
| `stayUnitKind` | `str` | Модель продажи: `NightRate` (за ночь) / `DailyRate` (за сутки) |
| `propertyKindId` | `int` | Тип средства размещения |
| `contactInfo.address` | `obj` | Адрес, координаты, почтовый индекс |
| `contactInfo.phones[]` | `obj` | Телефоны |
| `contactInfo.emails[]` | `str` | Email-адреса |
| `policy.checkInTime` / `checkOutTime` | `str` | Время заезда/выезда (hh:mm) |
| `timeZone.id` | `str` | Часовой пояс (напр. `Europe/London`) |
| `currency` | `str` | Валюта (напр. `GBP`) |
| `multiLocationProperty` | `bool` | Разные адреса у категорий номеров |
| `companyDetails` | `obj` | Юр. лицо, ИНН, ОГРН, юр. адрес |
| `amenities[]` | `obj` | Удобства: `code`, `displayName`, `chargeType` (free/chargeable/none) |
| `fsaCertifications[]` | `obj` | Реестр ФСА (РФ): статус сертификации, дата окончания |

**`ratePlans[]` — тарифы:**
| Поле | Тип | Описание |
|------|-----|---------|
| `id` | `str` | ID тарифа |
| `name` | `str` | Название |
| `description` | `str` | Описание тарифа (HTML) |
| `currency` | `str` | Валюта |
| `isStayWithChildrenOnly` | `bool` | Только с детьми |
| `cancellationRuleId` | `str` | ID правила отмены |
| `extraStayRuleId` | `str` | ID правила раннего заезда/позднего выезда |
| `vat.applicable` / `vat.included` / `vat.percent` | `bool`/`bool`/`int` | НДС: применяется / включён / ставка |
| `corporateOnly` | `bool` | Только для корпоративных клиентов |
| `availableServices[]` | `obj` | Доступные услуги: `id`, `included`, `roomTypeIds`, `roomTypeAvailability` |

**`roomTypes[]` — категории номеров:**
| Поле | Тип | Описание |
|------|-----|---------|
| `id` | `str` | ID категории |
| `name` | `str` | Название (напр. «Standard») |
| `description` | `str` | Описание |
| `amenities[].code` | `str` | Коды удобств (напр. `wifi_internet`) |
| `images[].url` | `str` | Фото номера |
| `size.value` | `int` | Площадь в м² |
| `categoryCode` / `categoryName` | `str` | Тип предложения (PlaceInRoom, Apartments, SmallHouse...) |
| `occupancy.adultBed` / `extraBed` / `childWithoutBed` | `int` | Вместимость |
| `placements[]` | `obj` | Размещение: `kind` (Adult/Child), `count`, `minAge`, `maxAge` |
| `position` | `int` | Порядок сортировки |
| `fsaCertification.index` | `int` | Индекс сертификации ФСА |

**`services[]` — услуги:**
| Поле | Тип | Описание |
|------|-----|---------|
| `id` | `str` | ID услуги |
| `name` | `str` | Название |
| `kind` | `str` | `Common` (любая) / `Meal` (питание) |
| `mealPlanCode` / `mealPlanName` | `str` | Код/название типа питания |
| `status` | `str` | `Active` / ... |
| `vat` | `obj` | НДС услуги |

---

### Search API — поиск размещения

#### `POST /v1/search` — поиск по минимальной цене (до 200 отелей)

Ищет самые дешёвые варианты по всем доступным отелям.

#### `GET /v1/properties/{propertyId}/search` — поиск по конкретному отелю

#### `POST /v1/properties/{propertyId}/search/extra-services` — доп. услуги

#### `POST /v1/properties/{propertyId}/search/extra-stays` — ранний заезд / поздний выезд

**Лимиты Search API:**
| Метод | Секунда | Минута | Час |
|-------|---------|--------|-----|
| Поиск по мин. цене | 3 | 20 | 900 |
| Поиск по отелю | 50 | 200 | 1000 |
| Доп. услуги / early/late | 10 | 50 | 500 |
| Получение доп. услуг | 10 | 100 | 1000 |

---

### CancellationRules — правила отмены

#### `GET /v1/properties/{propertyId}/cancellation-rules`

**Точки отсчёта (`referencePointKind`):**
- `ProviderArrivalTime` — стандартное время заезда отеля
- `ProviderDepartureTime` — стандартное время выезда отеля
- `GuestArrivalTime` — время заезда, указанное гостем
- `CustomArrivalTime` — произвольное время (`referencePointTime`)
- `BookingCreationTime` — время создания брони

**Периоды до точки отсчёта:**
- `NoMatter` — не важно
- `AtLeast` — больше или равно N
- `NoMoreThan` — меньше или равно N
- `Between` — в интервале [N..M]

**Единицы:** `Day` (дни), `Hour` (часы), `None`

**Режимы расчёта штрафа:**
- `NoPenalty` — без штрафа
- `FirstNightPercent` — % от стоимости первой ночи
- `PrepaymentPercent` — % от суммы предоплаты
- `FirstNights` — полная стоимость первых N ночей

---

### Public Reviews API — отзывы

#### `GET /v1/reviews` — список отзывов (пагинация, сортировка по дате/рейтингу)

#### `GET /v1/reviews/stats` — статистика (рейтинг, всего отзывов)

#### `GET /v1/reviews/sources` — источники отзывов

#### `GET /v1/reviews/categories` — категории оценок (excellent, good, poor)

---

### PMS API — управление отелем

| Группа | Методы |
|--------|--------|
| **Property** | Инвентарь отеля |
| **PropertyCompany** | Список компаний |
| **PropertyGuest** | Поиск гостей, профиль, карты лояльности, документы, история проживания |
| **PropertyReservation** | Детали брони, поиск, назначение комнат, check-in/out, оплата, возвраты, счета |
| **PropertyRoom** | Список комнат/номеров |

---

### PMS Analytics API

#### `GET /v1/analytics/occupancy` — ежедневная загрузка отеля

---

### Лимиты API

| API | Секунда | Минута | Час |
|-----|---------|--------|-----|
| Стандартные методы | 50 | 200 | 3000 |
| PMS Integration Storage | 50 | 1200 | 30000 |
| Public Reviews | 50 | 1200 | 30000 |
| Read Reservation (список) | 3 | 100 | 3000 |
| Read Reservation (детали) | 10 | 200 | 4000 |

Заголовки мониторинга:
- `x-ratelimit-remaining-second`
- `x-ratelimit-remaining-minute`
- `x-ratelimit-remaining-hour`
- `retry-after` (при 429)

---

### Итоговая сводная таблица по всем сервисам

| Критерий | RZD API | Tutu MCP | Yandex Travel | Skyscanner | Travelline |
|----------|---------|----------|---------------|------------|------------|
| **Поезда** | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Авиабилеты** | ❌ | ✅ | ❌ | ✅ | ❌ |
| **Отели** | ❌ | ✅ (search) | ✅ (цены + контент) | ✅ (цены + контент) | ✅ (PMS + цены) |
| **Аренда авто** | ❌ | ❌ | ❌ | ✅ | ❌ |
| **Автобусы** | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Электрички** | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Бронирование** | ❌ | ✅ (deeplink) | ✅ (booking_url) | ✅ (deepLink) | ✅ (PMS) |
| **Управление отелем** | ❌ | ❌ | ❌ | ❌ | ✅ (PMS) |
| **Глубина отелей** | — | Базовая | Средняя | Средняя | Максимальная |
| **Детализация номеров** | — | Название | Кровати + площадь | Базовая | Полная + FSA |
| **Правила отмены** | — | refund_type | refund_rules | — | Полные с периодами |
| **Отзывы** | — | Оценка + текст | — | Оценка + текст | Категории + источники |
| **Авторизация** | Без | Без | OAuth (партнёр) | API-ключ (заявка) | OAuth2.0 (партнёр) |
| **Протокол** | REST (rzd-api) | MCP (JSON-RPC) | REST | REST (create/poll) | REST |
| **MCP-сервер** | ❌ | ✅ (публичный) | ❌ | ✅ (по заявке) | ❌ |
| **Геопокрытие** | РФ | РФ + СНГ | РФ + мир | Глобальное (52 рынка) | РФ + мир |
| **Аналитика** | ❌ | ❌ | ❌ | ❌ | ✅ (occupancy) |

---

## Краткая сводка

### Что у кого брать (по вертикалям)

| Сервис | Поезда | Авиа | Отели | Авто | Автобусы | Электрички |
|--------|:------:|:----:|:-----:|:----:|:--------:|:----------:|
| **RZD API** | ✅ | — | — | — | — | — |
| **Tutu MCP** | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| **Yandex Travel** | — | — | ✅ | — | — | — |
| **Skyscanner** | — | ✅ | ✅ | ✅ | — | — |
| **Travelline** | — | — | ✅ | — | — | — |

### Доступ: открыто или нужно партнёрство

| Сервис | Доступ | Что нужно для подключения |
|--------|:------:|---------------------------|
| **RZD API** | 🟢 Открыто | Ничего — пакет `rzd-api` на PyPI |
| **Tutu MCP** | 🟢 Открыто | Ничего — публичный MCP-эндпоинт `https://mcp.tutu.ru/mcp` |
| **Yandex Travel** | 🔴 Партнёрство | OAuth-токен через Яндекс Дистрибуцию |
| **Skyscanner** | 🔴 Партнёрство | API-ключ через заявку партнёра |
| **Travelline** | 🔴 Партнёрство | OAuth2.0 `client_id` + `client_secret` от отельера или TravelLine |

### Наличие исторических данных

| Сервис | История | Примечание |
|--------|:------:|-----------|
| **RZD API** | ❌ | Только будущие продажи, нет эндпоинтов с прошедшими датами |
| **Tutu MCP** | ❌ | Только текущие предложения на будущее |
| **Yandex Travel** | ❌ | Поиск только на будущие даты |
| **Skyscanner** | ❌ | Только live-цены (create/poll) |
| **Travelline** | ~ | Внутренняя аналитика загрузки отеля, но не публичная история цен по рынку |

### Рекомендации по использованию

**Поезда:**
- Основной источник — **RZD API** (детальнее: номера мест, разные тарифы внутри вагона)
- Дополнительный — **Tutu MCP** (удобства, возвратные/невозвратные тарифы, отзывы о поезде)

**Отели:**
- Быстрый старт — **Tutu MCP** (открыт, без ключей, базовый поиск)
- Глубокие данные — **Travelline** (PMS, правила отмены, отзывы по категориям, FSA)
- Средний уровень — **Yandex Travel** (конфигурации кроватей, фильтры по карте)

**Авиабилеты:**
- Быстрый старт — **Tutu MCP** (открыт, без ключей)
- Глобальный охват — **Skyscanner** (52 рынка, 1300+ поставщиков, но партнёрство)

**Исторические данные:**
- Ни один сервис не предоставляет историю цен
- Единственный путь — свой сборщик: регулярно опрашивать открытые API и сохранять в БД

