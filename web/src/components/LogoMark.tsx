export function LogoMark() {
  return (
    <svg
      width="32"
      height="20"
      viewBox="0 0 32 20"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      {/* Upper-left interlocking rail */}
      <path
        d="M3.5 12.5L10.5 4.2C11.3 3.3 12.4 2.8 13.6 2.8H22.2C23.8 2.8 24.6 4.7 23.4 5.9L20.4 8.8"
        stroke="#0A74DF"
        strokeWidth="3.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Lower-right interlocking rail (180-deg symmetric) */}
      <path
        d="M28.5 7.5L21.5 15.8C20.7 16.7 19.6 17.2 18.4 17.2H9.8C8.2 17.2 7.4 15.3 8.6 14.1L11.6 11.2"
        stroke="#0A74DF"
        strokeWidth="3.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
