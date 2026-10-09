import unittest
from invoice import line_total


class InvoiceTests(unittest.TestCase):
    def test_multiple_items(self):
        self.assertEqual(line_total(3, 7), 21)

    def test_no_items(self):
        self.assertEqual(line_total(0, 7), 0)

    def test_single_item(self):
        self.assertEqual(line_total(1, 7), 7)


if __name__ == '__main__':
    unittest.main()
