import requests
from unidecode import unidecode

from parsel import Selector
from concurrent.futures import ThreadPoolExecutor, as_completed
from ugTranslate import translate_text
import pandas as pd
from datetime import datetime
import logging
import time
import re

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s: %(message)s')


class TuzilastvoBIHScraper:
    def __init__(self, max_workers=10, timeout=50):
        """
        Initialize the scraper with configurable threading and request parameters

        Args:
            max_workers (int): Number of concurrent threads
            timeout (int): Request timeout in seconds
        """
        self.max_workers = max_workers
        self.timeout = timeout

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        self.base_url = 'https://www.tuzilastvobih.gov.ba/index.php'
        self.all_entries = []

    def safe_translate(self, text, chunk_size=3000):
        """
        Safely translate text with chunk handling to avoid translation API limits

        Args:
            text (str): Text to translate
            chunk_size (int): Maximum chunk size for translation

        Returns:
            str: Translated text
        """
        if not text or len(text) < 3:
            return ''

        try:
            # Handle long texts by chunking
            if len(text) > chunk_size:
                # Split text into words to avoid breaking mid-word
                words = text.split()
                chunks = []
                current_chunk = ""

                for word in words:
                    if len(current_chunk) + len(word) + 1 > chunk_size:
                        chunks.append(current_chunk.strip())
                        current_chunk = word
                    else:
                        current_chunk += " " + word if current_chunk else word

                if current_chunk:
                    chunks.append(current_chunk.strip())

                # Translate each chunk and combine
                translated_chunks = []
                for chunk in chunks:
                    try:
                        translated_chunk = translate_text(chunk)['TranslatedText']
                        translated_chunks.append(translated_chunk)
                    except Exception as e:
                        logging.error(f"Translation error for chunk: {e}")
                        translated_chunks.append(chunk)

                return ' '.join(translated_chunks)

            # Translate single chunk
            return translate_text(text)['TranslatedText']

        except Exception as e:
            logging.error(f"Translation error: {e}")
            return text

    def format_date(self, date_str):
        """
        Format date and time from string

        Args:
            date_str (str): Date string to parse

        Returns:
            tuple: Formatted date and time
        """
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y. %H:%M")
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
        except Exception as e:
            logging.error(f"Date parsing error: {e}")
            return 'N/A', 'N/A'

    def fetch_page_content(self, page_num):
        """
        Fetch content for a specific page number

        Args:
            page_num (int): Page number to fetch

        Returns:
            list: Extracted news entries
        """
        try:
            url = f'{self.base_url}?opcija=arhiva&strana={page_num}&jezik=b'
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            response.raise_for_status()

            parsed_data = Selector(response.text)
            news_rows = parsed_data.xpath('//div[@class="inner2"]//div[@class="news"]')

            # Break if no news items found (last page)
            if not news_rows:
                return []

            page_entries = []
            for row in news_rows:
                entry = self.extract_news_entry(row)
                if entry:
                    page_entries.append(entry)

            return page_entries

        except requests.RequestException as e:
            logging.error(f"Request error on page {page_num}: {e}")
            return []

    def extract_news_entry(self, row):

        try:
            # Extract heading
            news_heading = ' '.join(row.xpath('.//h3//text()').getall()).strip()
            news_heading = unidecode(news_heading)
            # Get news URL
            news_url = row.xpath('.//h3//@href').get()
            if not news_url:
                return None

            full_url = f'{self.base_url}{news_url}'

            # Fetch detailed page
            response = requests.get(full_url, headers=self.headers, timeout=self.timeout)
            response.raise_for_status()

            detailed_page = Selector(response.text)

            # Extract details
            news_public_date_time = detailed_page.xpath('//div[@class="inner2"]//span[@class="date"]//text()').get()
            news_date, news_time = self.format_date(news_public_date_time)

            news_summary = ' '.join(
                detailed_page.xpath('//div[@class="inner2"]//div[@class="intro"]//text()').getall()).strip()
            news_summary = re.sub(r'\s+', ' ', news_summary)

            news_details = ' '.join(detailed_page.xpath(
                '//div[@class="inner2"]//div[not(@class="intro") and not(@id="slider") and not (@class="note") and not(@style="text-align: right")]//text()').getall()).strip()
            news_details = re.sub(r'\s+', ' ', news_details)
            # Translate in background
            return {
                "news_public_date": news_date,
                "news_public_time": news_time,
                "news_url": full_url,
                "news_heading": news_heading,
                "news_heading_translated": self.safe_translate(news_heading),
                "news_summary": news_summary,
                "news_summary_translated": self.safe_translate(news_summary),
                "news_details": news_details,
                "news_details_translated": self.safe_translate(news_details)
            }

        except Exception as e:
            logging.error(f"Error extracting news entry: {e}")
            return None

    def scrape_all_pages(self, ):
        url = 'https://www.tuzilastvobih.gov.ba/index.php?opcija=arhiva&strana=1&jezik=b'
        response = requests.get(url, headers=self.headers, timeout=self.timeout)
        parsed_page = Selector(response.text)
        total_page = parsed_page.xpath('//div[@class="pagination"]//a[10]//text()').get()
        max_pages = int(total_page)
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.fetch_page_content, page): page for page in range(1, max_pages + 1)}

            for future in as_completed(futures):
                page_entries = future.result()
                if not page_entries:
                    break
                self.all_entries.extend(page_entries)

        end_time = time.time()
        logging.info(f"Total entries scraped: {len(self.all_entries)}")
        logging.info(f"Total scraping time: {end_time - start_time:.2f} seconds")

        return self.all_entries

    def save_to_excel(self, filename="tuzilastvobih_gov.xlsx"):
        """
        Save scraped entries to Excel

        Args:
            filename (str): Output Excel filename
        """
        df = pd.DataFrame(self.all_entries)
        df.to_excel(filename, index=False, engine='openpyxl')
        logging.info(f"Saved {len(self.all_entries)} entries to {filename}")


def main():
    scraper = TuzilastvoBIHScraper(max_workers=10)

    scraper.scrape_all_pages()
    scraper.save_to_excel()


if __name__ == "__main__":
    main()